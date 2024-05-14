FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    g++ \
    unixodbc-dev \
    gnupg \
    curl \
    time \
    && apt-get clean;

RUN curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/10/prod.list > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql17

COPY . /app

RUN pip install Flask flask-cors requests pyodbc APScheduler python-dateutil

EXPOSE 8007

# Define environment variable
ENV DATABASE_IP=waltbotsqlserver

# Run app.py when the container launches
CMD ["python","-u", "app.py"]
