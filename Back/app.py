from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import pyodbc
import time
from apscheduler.schedulers.background import BackgroundScheduler
import re
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import uuid
from pytz import timezone

app = Flask(__name__)
CORS(app, resources={r"*": {"origins": "*"}})
scheduler = BackgroundScheduler(timezone=timezone('America/Sao_Paulo'))

DATABASE_NAME = 'DBNAME'
USERNAME = 'sa'
PASSWORD = 'yourpassword'
# Database connection details
DATABASE_IP = 'waltbotsqlserver'
DATABASE_PORT = '1433'
baseUrlWhatsapp = 'http://' + 'waha' + ':3000'

connection_string = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={DATABASE_IP},{DATABASE_PORT};DATABASE={DATABASE_NAME};UID={USERNAME};PWD={PASSWORD}'
@app.route('/user/register', methods=['POST'])
def register():
    data = request.json
    if not all(key in data for key in ['phoneNumber', 'secretCode', 'name']):
        return jsonify({'error': f'Missing data: {data}'}), 400

    phone_number = data['phoneNumber']
    verification_code = data['secretCode']
    name = data['name']
    chatId = get_chat_id(phone_number)

    if not chatId:
        return jsonify({'error': f"Tentativa de registro com dados: {data}, chatId inválido para o número {phone_number}"}), 400

    try:
        with pyodbc.connect(connection_string) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM [User] WHERE ChatId = ?", chatId)
                if cursor.fetchone():
                    return jsonify({'error': f"Usuário já está registrado."}), 409
                
                cursor.execute("""
                    INSERT INTO [User] (ChatId, UserName, VerificationCode, CreationTime, Verified, Role, BlackListed) VALUES (?, ?, ?, CONVERT(DATETIME, ?, 120), 0, 'User',0)
                """, chatId, name, verification_code, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                conn.commit()

                cursor.execute("SELECT UserId FROM [User] WHERE ChatId = ?", chatId)
                userId = cursor.fetchone()[0]

                send_whatsapp_message(chatId, f"Olá {name}\nSeu código de verificação é: *{verification_code}*\nResponda à esta mensagem com o código de verificação para garantir sua identidade e verificar seu acesso ao WaltBot 🤖.")
                
                return jsonify({'message': 'Cadastro realizado com sucesso', 'userId': userId}), 200
    
    except Exception as e:
        return jsonify({'error': 'Database operation failed'}), 500
    
@app.route('/user/login', methods=['POST'])
def login():
    data = request.json
    phone_number = data.get('phoneNumber')
    secret_code = data.get('secretCode')

    if not phone_number or not secret_code:
        return jsonify({'error': 'É necessário informar número de telefone e palavra secreta'}), 400

    chat_id = get_chat_id(phone_number)
    if not chat_id:
        return jsonify({'error': 'Numero de telefone inválido, certifique-se de botar o código do país 55 (Brasil) e seu DDD'}), 400

    try:
        with pyodbc.connect(connection_string) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT UserId, Verified FROM [User] WHERE ChatId = ? AND VerificationCode = ?", chat_id, secret_code)
                user = cursor.fetchone()
                if user:
                    if user.Verified:
                        return jsonify({'message': 'Login successful', 'userId': user.UserId}), 200
                    else:
                        send_whatsapp_message(user.UserId, f"Sua conta ainda não está verificada, para fazer login é necessário enviar uma mensagem neste número com a sua palavra secreta: *{secret_code}*")
                        return jsonify({'error': 'Usuário não verificado, verifique seu whatsapp'}), 401
                else:
                    return jsonify({'error': 'Senha ou usuário inválidos'}), 404
    except Exception as e:
        return jsonify({'error': 'Database operation failed'}), 500
    
@app.route('/user/rememberCode', methods=['POST'])
def remember_code():
    data = request.json
    phone_number = data.get('phoneNumber')

    if not phone_number:
        return jsonify({'error': 'Número de telefone é obrigatório'}), 400

    chat_id = get_chat_id(phone_number)
    if not chat_id:
        return jsonify({'error': 'Número de telefone inválido'}), 400

    try:
        with pyodbc.connect(connection_string) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT VerificationCode FROM [User] WHERE ChatId = ?", chat_id)
                result = cursor.fetchone()
                if result:
                    send_whatsapp_message(chat_id, f"O seu código secreto é: '{result.VerificationCode}'. 🤫 Não compartilhe ele com ninguém para não receber mensagens indesejadas!")
                    return jsonify({'message': 'Código secreto enviado com sucesso'}), 200
                else:
                    return jsonify({'error': 'Usuário não encontrado'}), 404
    except Exception as e:
        return jsonify({'error': 'Database operation failed'}), 500
    
def delete_message_and_dependencies(message_id):
    try:
        with pyodbc.connect(connection_string) as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM [MessageConfirmation] WHERE MessageScheduleId IN (SELECT MessageScheduleId FROM [MessageSchedule] WHERE MessageId = ?)", (message_id,))
                cursor.execute("DELETE FROM [MessageSchedule] WHERE MessageId = ?", (message_id,))
                cursor.execute("DELETE FROM [Message] WHERE MessageId = ?", (message_id,))
                conn.commit()
    except Exception as e:
        print(e)
@app.route('/messages/insert', methods=['POST'])
def insert_user_message():
    data = request.json
    message_text = data.get('message')
    tag = data.get('tag')
    status = data.get('status')
    message_id = data.get('messageId')
    schedules = data.get('schedules', [])
    user_id = data.get('userId')
    deletionTime = data.get('deletionTime')

    if not user_id:
        return jsonify({'error': 'UserID is required'}), 400
    try:
        with pyodbc.connect(connection_string) as conn:
            with conn.cursor() as cursor:
                if message_id:
                    cursor.execute("SELECT * FROM [Message] WHERE MessageId = ?", (message_id))
                    messageDB = cursor.fetchone()
                    if not messageDB:
                        return jsonify({'error': 'Message not found'}), 404

                    if deletionTime:
                        delete_message_and_dependencies(message_id)
                        return jsonify({'message': 'Message deleted successfully'}), 200
                    else:
                        cursor.execute("""
                            UPDATE [Message] SET Message = ?, Tag = ?, Status = ?, DeletionTime = CONVERT(DATETIME, ?, 120) WHERE MessageId = ?
                        """, (message_text if message_text else messageDB.Message, tag if tag else messageDB.Tag, 
                            status if status else messageDB.Status, deletionTime.strftime('%Y-%m-%d %H:%M:%S') if deletionTime else messageDB.DeletionTime, message_id))
                        conn.commit()

                else:
                    message_id = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO [Message] (MessageId, UserId, Message, Tag, CreationTime, Status)
                        VALUES (?, ?, ?, ?, CONVERT(DATETIME, ?, 120), ?)
                    """, (message_id, user_id, message_text, tag, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), status))
                    conn.commit()

                cursor.execute("SELECT * FROM [MessageSchedule] WHERE MessageId = ?", (message_id))
                existing_schedules = cursor.fetchall()

                for key in existing_schedules:
                    try:
                        remove_scheduled_message(key.JobId)
                    except Exception as e:
                        print(f"Error removing scheduled message: {e}")
                    cursor.execute("DELETE FROM [MessageConfirmation] WHERE MessageScheduleId = ?",
                            (key.MessageScheduleId))
                    cursor.execute("DELETE FROM [MessageSchedule] WHERE MessageScheduleId = ?",
                                (key.MessageScheduleId))

                conn.commit()

                # Insert or update schedules
                for schedule in schedules:
                        # Insert new schedule
                        message_schedule_id = str(uuid.uuid4())
                        schedule['scheduledTime'] = schedule['scheduledTime'].replace('T', ' ')
                        try:
                            schedule_time = datetime.strptime(schedule['scheduledTime'], '%Y-%m-%d %H:%M')
                        except:
                            schedule_time = datetime.strptime(schedule['scheduledTime'], '%Y-%m-%d %H:%M:%S')
                        job_id = schedule_message(handle_schedule, schedule_time, message_schedule_id)
                        print(f"Scheduled job with ID: {job_id}")
                        cursor.execute("""
                            INSERT INTO [MessageSchedule] (MessageScheduleId, MessageId, ScheduledTime, RecurrenceType, RecurrenceValue, Status, RequireConfirmation, ConfirmationTag , ConfirmationRecurrenceInMinutes, JobId)
                            VALUES (?,?, CONVERT(DATETIME, ?, 120), ?, ?, ?, ?, ?, ?, ?)
                        """, (message_schedule_id, message_id, schedule['scheduledTime'], schedule['recurrenceType'], schedule['recurrenceValue'], 
                            'Inactive' if not status else schedule['status'], schedule['requireConfirmation'], schedule['confirmationTag'] ,schedule['confirmationRecurrenceInMinutes'], job_id))
                        conn.commit()
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'message': 'Mensagem atualizada com sucesso'}), 200
@app.route('/user/<user_id>/messages', methods=['GET'])
def get_user_messages(user_id):
    result = {}  
    try:
        with pyodbc.connect(connection_string) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT m.MessageId, m.UserId, m.Message, m.Tag, m.Status, m.CreationTime,
                ms.MessageScheduleId, ms.ScheduledTime, ms.RecurrenceType, ms.RecurrenceValue, ms.Status AS ScheduleStatus
                ,ms.RequireConfirmation, ms.ConfirmationTag ,ms.ConfirmationRecurrenceInMinutes
                FROM [Message] m
                LEFT JOIN [MessageSchedule] ms ON m.MessageId = ms.MessageId
                WHERE m.UserId = ? AND m.DeletionTime IS NULL
                ORDER BY ms.ScheduledTime ASC
            """, (user_id,))
            rows = cursor.fetchall()
            for row in rows:
                message_id = row.MessageId
                if message_id not in result:
                    result[message_id] = {
                        'messageId': str(message_id),
                        'userId': str(row.UserId),
                        'message': row.Message,
                        'tag': row.Tag,
                        'status': row.Status,
                        'creationTime': row.CreationTime.strftime('%Y-%m-%d %H:%M:%S'),
                        'schedules': []
                    }
                if row.MessageScheduleId:  
                    result[message_id]['schedules'].append({
                        'messageScheduleId': str(row.MessageScheduleId),
                        'scheduledTime': row.ScheduledTime.strftime('%Y-%m-%d %H:%M:%S'),
                        'recurrenceType': row.RecurrenceType,
                        'recurrenceValue': row.RecurrenceValue,
                        'status': row.ScheduleStatus,
                        'requireConfirmation': row.RequireConfirmation,
                        'confirmationTag': row.ConfirmationTag,
                        'confirmationRecurrenceInMinutes': row.ConfirmationRecurrenceInMinutes
                    })
        return jsonify(list(result.values())), 200
    except pyodbc.Error as e:
        return jsonify({'error': 'Database operation failed'}), 500

@app.route('/user/<user_id>/confirmationtags', methods=['GET'])
def get_user_confirmation_tags(user_id):
    if not user_id:
        return jsonify({'error': 'UserID is required'}), 400
    try:
        with pyodbc.connect(connection_string) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT ms.ConfirmationTag
                    FROM [MessageSchedule] ms
                    JOIN [Message] m ON ms.MessageId = m.MessageId
                    JOIN [User] u ON m.UserId = u.UserId
                    WHERE u.UserId = ? AND ms.Status = 'Active' AND m.Status = 'Active'
                """, user_id)
                confirmation_tags = [row.ConfirmationTag for row in cursor.fetchall()]
                return jsonify({'confirmationTags': confirmation_tags}), 200
    except pyodbc.Error as e:
        return jsonify({'error': 'Database operation failed'}), 500

@app.route('/whatsappWebhook', methods=['POST'])
def whatsappWebhook():
    data = request.json
# Check for non-message events or broadcasts
    fromCheck = data.get('payload', {}).get('from')
    messageType = data.get('payload', {}).get('message_type', 'text')
    
    if not fromCheck or fromCheck == "status@broadcast" or messageType != 'text':
        return jsonify({'message': 'OK'}), 200
    
    chatID = fromCheck
    try:
        message_text = data['payload']['body'].strip().lower()
    except:
        return jsonify({'message': 'OK'}), 200

    try:
        with pyodbc.connect(connection_string) as conn:
            with conn.cursor() as cursor:
                # Check if user exists and get their verification status
                cursor.execute("""
                    SELECT UserId, VerificationCode, Verified FROM [User] WHERE ChatId = ?
                """, chatID)
                user = cursor.fetchone()

                if not user:
                    return jsonify({'message': 'OK'}), 200
                
                if not user.Verified:
                    if message_text == user.VerificationCode.strip().lower():
                        cursor.execute("""
                            UPDATE [User] SET Verified = 1, VerificationTime = CONVERT(DATETIME, ?, 120)
                            WHERE UserId = ?
                        """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user.UserId))
                        conn.commit()
                        send_whatsapp_message(chatID, "Seu número foi verificado com sucesso.")
                    else:
                        send_whatsapp_message(chatID, "Código inválido. Seu código de verificação é " + user.VerificationCode + ". Tente novamente.")
                    return jsonify({'message': 'OK'}), 200
                
                # If the user is verified, check for pending confirmations
                cursor.execute("""
                    SELECT mc.MessageConfirmationId, mc.ConfirmationTag, mc.JobId
                    FROM [MessageConfirmation] mc
                    JOIN [MessageSchedule] ms ON mc.MessageScheduleId = ms.MessageScheduleId
                    JOIN [Message] m ON ms.MessageId = m.MessageId
                    JOIN [User] u ON m.UserId = u.UserId 
                    WHERE u.ChatId = ? AND ms.Status = 'Active' AND m.Status = 'Active'
                """, chatID)
                pendingConfirmations = cursor.fetchall()

                if pendingConfirmations:
                    for confirmation in pendingConfirmations:
                        if message_text == confirmation.ConfirmationTag.strip().lower():
                            cursor.execute("DELETE FROM [MessageConfirmation] WHERE MessageConfirmationId = ?", (confirmation.MessageConfirmationId,))
                            conn.commit()
                            send_whatsapp_message(chatID, "Mensagem confirmada, agendamento repetitivo cancelado.")
                            remove_scheduled_message(confirmation.JobId)
                            return jsonify({'message': 'Confirmation matched and deleted'}), 200

                    send_whatsapp_message(chatID, "Código de confirmação inválido, abaixo estão suas tags de confirmação pendentes.\n" + "\n".join([confirmation.ConfirmationTag for confirmation in pendingConfirmations]))

                if chatID == "yourchatid@c.us":
                    if message_text == "syncSchedules".strip().lower():
                        sync_jobs_schedules()
                        send_whatsapp_message(chatID, "Agendamentos sincronizados com sucesso.")
                    elif message_text == "showSchedules".strip().lower():
                        jobs = view_scheduled_messages()
                        send_whatsapp_message(chatID, "Scheduled jobs:" + "\n".join(jobs) + "\n")
                    else:
                        send_whatsapp_message(chatID, f"Comando inválido {message_text}. Comandos disponíveis: syncSchedules, showSchedules")
                return jsonify({'message': 'OK'}), 200

    except Exception as e:
        print(f"Error during processing: {e}")
        return jsonify({'error': str(e)}), 500
    
@app.route('/johnCena', methods=['POST'])
def sendWpp(phone_number, message):
    data = request.json
    message = data.get('message')
    phone_number = data.get('phoneNumber')
    chatid = get_chat_id(phone_number)
    api_url = f"{baseUrlWhatsapp}/api/sendText"  
    payload = {
        "chatId": chatid,
        "text": message,
        "session": "default"
    }
    response = requests.post(api_url, json=payload)

    return response
def handle_schedule(messageScheduleId, job_id): 

    with pyodbc.connect(connection_string) as conn:
        with conn.cursor() as cursor:
            cursor.execute("""SELECT ms.*
                           , m.Message, m.MessageId
                           , u.ChatId
                           FROM [MessageSchedule] ms
                           LEFT JOIN  [Message] m ON m.MessageId = ms.MessageId
                           LEFT JOIN [User] u ON u.UserId = m.UserId
                           WHERE ms.MessageScheduleId = ?""", messageScheduleId)
            messageSchedule = cursor.fetchone()
            if not messageSchedule:
                return
        
            enviado = send_whatsapp_message(messageSchedule.ChatId, messageSchedule.Message + f"\nTag de confirmação: *{messageSchedule.ConfirmationTag}*")

            if messageSchedule.RequireConfirmation:
                nextSendConfirmation = (messageSchedule.ScheduledTime + timedelta(minutes=messageSchedule.ConfirmationRecurrenceInMinutes))
                message_confirmation_id = str(uuid.uuid4())
                confirmation_job_id = schedule_message(handle_confirmation, nextSendConfirmation, message_confirmation_id)
                cursor.execute("""
                    INSERT INTO [MessageConfirmation] (MessageConfirmationId, MessageScheduleId, MessageId, ConfirmationTag, InsertionTime, RecurrenceValueInMinutes, JobId) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (message_confirmation_id,messageScheduleId, messageSchedule.MessageId, messageSchedule.ConfirmationTag, nextSendConfirmation.strftime('%Y-%m-%d %H:%M:%S'), messageSchedule.ConfirmationRecurrenceInMinutes, confirmation_job_id))
                conn.commit()

            try:
                removido = remove_scheduled_message(job_id)
            except:
                print("Erro ao remover mensagem agendada")

            if messageSchedule.RecurrenceType == 'None':
                return

            elif messageSchedule.RecurrenceType == 'Minute':
                schedule_time = messageSchedule.ScheduledTime + timedelta(minutes=messageSchedule.RecurrenceValue)

            elif messageSchedule.RecurrenceType == 'Hour':
                schedule_time = messageSchedule.ScheduledTime + timedelta(hours=messageSchedule.RecurrenceValue)

            elif messageSchedule.RecurrenceType == 'Day':
                schedule_time = messageSchedule.ScheduledTime + timedelta(days=messageSchedule.RecurrenceValue)

            elif messageSchedule.RecurrenceType == 'Week':
                schedule_time = messageSchedule.ScheduledTime + timedelta(weeks=messageSchedule.RecurrenceValue)

            elif messageSchedule.RecurrenceType == 'Month':
                schedule_time = messageSchedule.ScheduledTime + relativedelta(months=messageSchedule.RecurrenceValue)

            try:
                with pyodbc.connect(connection_string) as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("""
                        UPDATE [MessageSchedule] SET ScheduledTime = CONVERT(datetime, ?, 120) WHERE MessageScheduleId = ?
                        """, (schedule_time.strftime('%Y-%m-%d %H:%M:%S'), messageSchedule.MessageScheduleId))
                        conn.commit()
            except Exception as e:
                print(f"Erro func handle_schedule: {e}")
                send_whatsapp_message("556196571370@c.us",f"Erro func handle_schedule: {e}")

            job_id = schedule_message(handle_schedule, schedule_time, messageSchedule.MessageScheduleId)

def handle_confirmation(messageConfirmationId, job_id): 

    with pyodbc.connect(connection_string) as conn:
        with conn.cursor() as cursor:
            cursor.execute("""SELECT mc.*
                           , m.Message, m.MessageId
                           , u.ChatId
                           FROM [MessageConfirmation] mc
                           LEFT JOIN  [Message] m ON m.MessageId = mc.MessageId
                           LEFT JOIN [User] u ON u.UserId = m.UserId
                           WHERE mc.MessageConfirmationId = ?
                           AND m.Status = 'Active' AND u.Verified = 1 AND u.BlackListed = 0
                           """, messageConfirmationId)
            messageConfirmation = cursor.fetchone()
            if not messageConfirmation:
                return
        
            enviado = send_whatsapp_message(messageConfirmation.ChatId, messageConfirmation.Message)

            removido = remove_scheduled_message(job_id)

            job_id = schedule_message(handle_confirmation,(messageConfirmation.InsertionTime + timedelta(minutes=messageConfirmation.RecurrenceValueInMinutes)), messageConfirmation.MessageConfirmationId)
            cursor.execute(""" UPDATE [MessageConfirmation] SET InsertionTime = CONVERT(DATETIME, ?,120), JobId = ? WHERE MessageConfirmationId = ? """, ((messageConfirmation.InsertionTime + timedelta(minutes=messageConfirmation.RecurrenceValueInMinutes)).strftime('%Y-%m-%d %H:%M:%S'),job_id,messageConfirmation.MessageConfirmationId))
            conn.commit()

def sync_jobs_schedules():
    with pyodbc.connect(connection_string) as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT 
                ms.MessageScheduleId, ms.ScheduledTime, ms.RecurrenceType, ms.RecurrenceValue
                , ms.RequireConfirmation, ms.ConfirmationTag, ms.ConfirmationRecurrenceInMinutes
                , m.MessageId, m.UserId, u.ChatId, m.Message           
                FROM [MessageSchedule] ms
                LEFT JOIN [Message] m ON m.MessageId = ms.MessageId
                LEFT JOIN [User] u ON u.UserId = m.UserId
                WHERE ms.Status = 'Active' AND m.Status = 'Active' 
                AND u.Verified = 1 AND m.DeletionTime IS NULL
                AND u.BlackListed = 0
                           """)
            schedules = cursor.fetchall()
            scheduler.remove_all_jobs()
            for schedule in schedules:
                jobId = schedule_message(handle_schedule, schedule.ScheduledTime, schedule.MessageScheduleId)
                cursor.execute(""" UPDATE [MessageSchedule] SET JobId = ? WHERE MessageScheduleId = ? """, (jobId, schedule.MessageScheduleId))

            conn.commit() 

# DO NOT MODIFY THE CODE BELOW THIS LINE
def schedule_message(function, scheduledTime, messageScheduleId):
    job = scheduler.add_job(function, 'date', run_date=scheduledTime, args=[messageScheduleId, None])
    job.modify(args=[messageScheduleId, job.id])
    return job.id

def remove_scheduled_message(job_id):
    try:
        scheduler.remove_job(job_id)
        print(f"Job {job_id} successfully removed")
    except Exception as e:
        print(f"Error removing scheduled message: {e}")

def check_database_connection():
    connected = False
    while not connected:
        try:
            # Attempt to connect to the database
            with pyodbc.connect(connection_string) as conn:
                print("Successfully connected to the database.")
                # Since the connection was successful, set connected to True
                connected = True
        except pyodbc.Error as e:
            print("Failed to connect to the database. Error:", e)
            print("Trying again in 2 seconds...")
            time.sleep(2)  # Wait for half a second before retrying   

def initialize_database(surely = False):
    if not surely:
        return True
    try:
        with pyodbc.connect(connection_string) as conn:
            with conn.cursor() as cursor:
                cursor.execute("USE [DBWALT];")
                cursor.commit()

                print("Creating tables...")
                # Create tables
                cursor.execute("""
                    CREATE TABLE [User] (
                        UserId UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWID(),
                        ChatId VARCHAR(50),
                        UserName VARCHAR(100),
                        VerificationCode VARCHAR(100),
                        CreationTime DATETIME,
                        VerificationTime DATETIME,
                        Verified BIT DEFAULT 0, -- 0 = Not Verified, 1 = Verified
                        Role VARCHAR(50),
                        BlackListed BIT
                    );
                """)
                cursor.execute("""
                    CREATE TABLE [Message] (
                        MessageId UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWID(),
                        UserId UNIQUEIDENTIFIER FOREIGN KEY REFERENCES [User](UserId),
                        Message TEXT,
                        Tag TEXT,
                        CreationTime DATETIME,
                        DeletionTime DATETIME NULL,
                        Status VARCHAR(50) DEFAULT 'Inactive'
                    );
                """)
                cursor.execute("""
                    CREATE TABLE [MessageSchedule] (
                        MessageScheduleId UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWID(),
                        MessageId UNIQUEIDENTIFIER FOREIGN KEY REFERENCES [Message](MessageId),
                        ScheduledTime DATETIME,
                        RecurrenceType VARCHAR(50) DEFAULT 'Minute',
                        RecurrenceValue INT,
                        Status VARCHAR(50) DEFAULT 'Active',
                        RequireConfirmation BIT DEFAULT 0,
                        ConfirmationRecurrenceInMinutes INT,
                        ConfirmationTag NVARCHAR(100),
                        JobId VARCHAR(200)
                    );
                """)
                cursor.execute("""
                    CREATE TABLE [MessageConfirmation] (
                        MessageConfirmationId UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWID(),
                        MessageScheduleId UNIQUEIDENTIFIER FOREIGN KEY REFERENCES [MessageSchedule](MessageScheduleId),
                        MessageId UNIQUEIDENTIFIER FOREIGN KEY REFERENCES [Message](MessageId),
                        ConfirmationTag NVARCHAR(100),
                        InsertionTime DATETIME,
                        RecurrenceValueInMinutes INT,
                        JobId VARCHAR(200)
                    );
                """)
                cursor.execute("""
                    CREATE TABLE [SentMessages] (
                        Id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWID(),
                        UserId UNIQUEIDENTIFIER FOREIGN KEY REFERENCES [User](UserId),
                        Message TEXT,
                        SentTime DATETIME,
                        Status VARCHAR(50) DEFAULT 'Pending',
                        Response TEXT,
                        ResponseStatusCode INT
                    );
                """)
                cursor.commit()
                print("Tables created.")
        print("Database initialized successfully.")
        return True
    except Exception as e:
        print(f"Failed to initialize the database. Error: {e}")
        return False

def send_whatsapp_message(chat_id,message):
    try:
        chatid = get_chat_id(chat_id)
        api_url = f"{baseUrlWhatsapp}/api/sendText"  
        payload = {
            "chatId": chatid,
            "text": message,
            "session": "default"
        }
        response = requests.post(api_url, json=payload)
        return True
    except Exception as e:
        print(f"Error during processing: {e}")
        return False

def extract_digits(phone_number):
    digits = re.findall(r'\d+', phone_number)
    return ''.join(digits)

def get_chat_id(phone_number):
    print(f"get_chat_id com phone_number: {phone_number}")
    if '@' in phone_number:
        phone_number = phone_number.split('@')[0]
    phone_number = extract_digits(phone_number)
    api_url = f"{baseUrlWhatsapp}/api/checkNumberStatus?session=default&phone={phone_number}"  # Adjust as necessary for Docker networking
    print(f"get_chat_id url: {api_url}")
    response = requests.get(api_url)
    print(f"get_chat_id url response: {response}")
    if response.ok:
        try:
            data = response.json()
            chatid = data['chatId']
            return chatid
        except:
            return None

def view_scheduled_messages():
    try:
        jobs = scheduler.get_jobs()
        print('Scheduled jobs:')
        result = [f'Job ID: {job.id}, Next Run: {job.next_run_time}' for job in jobs]
        for job in jobs:
            print(result)
            return result
    except Exception as e:
        print(f"Error accessing scheduled jobs: {e}")

def start_scheduler():
    try:
        scheduler.start()
    except Exception as e:
        print(f"Error starting scheduler: {e}")

if __name__ == '__main__':
    #CUIDADOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOO
    initialized = initialize_database(False)
    if not initialized:
        print("Failed to initialize the database. Exiting...")
        exit(1)
    #CUIDADOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOO
    view_scheduled_messages()
    with app.app_context():
        start_scheduler()
    check_database_connection()
    #sync_jobs_schedules()
    #view_scheduled_messages()
    app.run(host='0.0.0.0', port=8007)
