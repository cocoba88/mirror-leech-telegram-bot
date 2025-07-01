FROM anasty17/mltb:latest

WORKDIR /usr/src/app
RUN chmod 777 /usr/src/app

RUN python3 -m venv mltbenv

COPY requirements.txt .
RUN mltbenv/bin/pip install --no-cache-dir -r requirements.txt
# Install Playwright
# Buat virtual environment setelah file disalin
RUN python3 -m venv mltbenv && \
    mltbenv/bin/pip install --no-cache-dir -r requirements.txt && \
    mltbenv/bin/playwright install-deps chromium && \
    mltbenv/bin/playwright install chromium

COPY . .

CMD ["bash", "start.sh"]
