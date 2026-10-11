FROM python:3.15-rc-alpine3.22

ENV TZ=Asia/Dhaka PYTHONUNBUFFERED=1
WORKDIR /app

COPY app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./

EXPOSE 5000
CMD ["python", "app.py"]