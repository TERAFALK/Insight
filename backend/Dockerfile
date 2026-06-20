FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libpangocairo-1.0-0 \
    libcairo2 libgobject-2.0-0 \
    libharfbuzz0b libharfbuzz-subset0 libfontconfig1 \
    libjpeg62-turbo libopenjp2-7 \
    libffi-dev shared-mime-info fonts-liberation \
    && ( apt-get install -y --no-install-recommends libgdk-pixbuf2.0-0 \
         || apt-get install -y --no-install-recommends libgdk-pixbuf-xlib-2.0-0 ) \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/reports_output

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
