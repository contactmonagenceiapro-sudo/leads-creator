FROM python:3.10-slim
WORKDIR /app

# Outils de compilation nécessaires à certaines dépendances (ex: fpdf2)
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Contexte de build = racine du dépôt (cf. docker-compose.yml : build.context
# = "."). Un seul requirements.txt fait foi pour tout le projet (host +
# conteneur) — évite la divergence silencieuse déjà observée entre ce
# fichier et l'ancien api/requirements.txt (stripe/fpdf2/beautifulsoup4
# absents de ce dernier, jamais détectée car le code qui les importe a été
# ajouté après la dernière reconstruction de l'image).
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
