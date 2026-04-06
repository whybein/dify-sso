FROM python:3.11-alpine AS running

ENV LANG='en_US.UTF-8'
ENV LANGUAGE='en_US.UTF-8'
ENV TZ='Asia/Seoul'
ENV GUNICORN_WORKERS=2

RUN apk --update -t --no-cache add tzdata libpq
RUN ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime
RUN echo "${TZ}" > /etc/timezone
RUN apk add --no-cache --virtual .build-deps gcc python3-dev musl-dev postgresql-dev
RUN pip install --upgrade pip
RUN pip install --no-cache-dir psycopg2-binary
RUN apk del --no-cache .build-deps

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "exec gunicorn -w ${GUNICORN_WORKERS} -b 0.0.0.0:8000 app.main:app"]
