# Business Automation System

Ce système automatise le pilotage de votre activité en générant des rapports stratégiques hebdomadaires basés sur vos données réelles.

## Installation rapide
1. Assurez-vous d'avoir Python 3.10+ installé.
2. Créez un environnement virtuel : `python3 -m venv venv && source venv/bin/activate`.
3. Installez les dépendances : `pip install requests python-dotenv`.
4. Copiez le modèle : `cp .env.example .env`.
5. Modifiez le fichier `.env` avec vos accès (Supabase, Email, etc.).
6. Lancez l'analyse : `python3 ceo_agent.py`.
