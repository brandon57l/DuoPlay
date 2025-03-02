from flask import Flask, request, render_template, jsonify
import requests
from flask_sqlalchemy import SQLAlchemy
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound, TooManyRequests
import yt_dlp
import time
import re
import os
import json

app = Flask(__name__)

# Récupération de la clé API depuis l'environnement (ou mettez-la en dur pour tester)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyBiZONd6VA8y9zAd8vueZRo_IrPnn7iHlw")  # Remplacez par votre clé
# URL de l'API Gemini avec la clé
GEMINI_API_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"

# 🔹 Configuration de la base de données SQLite (modifiable pour PostgreSQL, MySQL, etc.)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///transcripts.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# 🔹 Modèle SQLAlchemy pour stocker les transcriptions
class Transcript(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.String(20), nullable=False, index=True)
    langue = db.Column(db.String(5), nullable=False)  # 'en' ou 'zh'
    texte = db.Column(db.Text, nullable=False)

    def __repr__(self):
        return f"<Transcript {self.video_id} - {self.langue}>"

# 🔹 Création des tables si elles n'existent pas
with app.app_context():
    db.create_all()

def extract_video_id(youtube_url):
    """Extrait l'ID de la vidéo YouTube à partir d'une URL valide."""
    regex = r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:[^\/]+\/.*|(?:v|e(?:mbed)?)\/|.*[?&]v=)|youtu\.be\/)([^\"&?/ ]{11})"
    match = re.search(regex, youtube_url)
    return match.group(1) if match else None

def get_transcript_from_db(video_id, langue):
    """Récupère une transcription depuis la base de données si elle existe."""
    transcript = Transcript.query.filter_by(video_id=video_id, langue=langue).first()
    return transcript.texte if transcript else None

def save_transcript_to_db(video_id, langue, texte):
    """Enregistre une transcription dans la base de données."""
    new_transcript = Transcript(video_id=video_id, langue=langue, texte=texte)
    db.session.add(new_transcript)
    db.session.commit()

def get_transcript(video_id):
    """Récupère les transcriptions en anglais et en chinois, avec gestion des erreurs et stockage en base."""
    transcript_data_en = get_transcript_from_db(video_id, 'en')
    transcript_data_ch = get_transcript_from_db(video_id, 'zh')

    # Si les transcriptions existent déjà, on les retourne
    if transcript_data_en and transcript_data_ch:
        return {"en": eval(transcript_data_en), "ch": eval(transcript_data_ch)}

    # Sinon, on va les chercher via l'API YouTube
    transcript_data_en, transcript_data_ch = [], []

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        # Récupérer la transcription en anglais
        try:
            transcript_en = transcript_list.find_transcript(['en']).fetch()
            save_transcript_to_db(video_id, 'en', str(transcript_en))  # Sauvegarde
            transcript_data_en = transcript_en
        except NoTranscriptFound:
            transcript_data_en = [{"text": "Aucune transcription anglaise trouvée.", "start": 0, "duration": 0}]
        except Exception as e:
            transcript_data_en = [{"text": f"Erreur en anglais: {e}", "start": 0, "duration": 0}]

        # Récupérer la transcription en chinois
        try:
            transcript_ch = transcript_list.find_transcript(['zh-CN', 'zh']).fetch()
            save_transcript_to_db(video_id, 'zh', str(transcript_ch))  # Sauvegarde
            transcript_data_ch = transcript_ch
        except NoTranscriptFound:
            transcript_data_ch = [{"text": "Aucune transcription chinoise trouvée.", "start": 0, "duration": 0}]
        except Exception as e:
            transcript_data_ch = [{"text": f"Erreur en chinois: {e}", "start": 0, "duration": 0}]

    except TooManyRequests:
        time.sleep(5)  # Attendre 5 secondes en cas de blocage
        return get_transcript(video_id)  # Réessayer après la pause

    except TranscriptsDisabled:
        return {"error": "Les sous-titres sont désactivés pour cette vidéo."}

    except Exception as e:
        return {"error": f"Erreur inattendue : {e}"}

    return {"en": transcript_data_en, "ch": transcript_data_ch}

@app.route('/', methods=['GET'])
def index():
    transcript_data_en, transcript_data_ch = [], []
    youtube_url = request.args.get('youtube_url', '').strip()
    error_message = None

    if youtube_url:
        video_id = extract_video_id(youtube_url)
        if not video_id:
            error_message = "URL YouTube invalide."
        else:
            result = get_transcript(video_id)
            if "error" in result:
                error_message = result["error"]
            else:
                transcript_data_en = result.get("en", [])
                transcript_data_ch = result.get("ch", [])

    return render_template('index.html',
                           transcript_data_en=transcript_data_en,
                           transcript_data_ch=transcript_data_ch,
                           youtube_url=youtube_url,
                           error_message=error_message)

@app.route('/send_message', methods=['POST'])
def send_message():
    # Récupérer le message et l'historique envoyé par le client
    message = request.form.get('message')
    history = request.form.get('history')
    
    # Convertir l'historique en liste d'objets (s'il existe)
    conversation_history = json.loads(history) if history else []
    
    # Ajouter le message de l'utilisateur à l'historique (seul le texte est conservé pour l'API)
    conversation_history.append({'text': message})
    
    # Préparer la requête à l'API Gemini en envoyant tout l'historique
    headers = {'Content-Type': 'application/json'}



    data = {
        "contents": [
            {
                "role": "model",
                "parts": [{"text": "Context : App de transcription youtube."}]  # Le contexte ajouté ici
            },
            {
                "role": "user",  # Message de l'utilisateur
                "parts": [{"text": msg['text']} for msg in conversation_history]  # Historique des messages
            }
        ]
    }
    
    try:
        response = requests.post(GEMINI_API_ENDPOINT, json=data, headers=headers)
        gemini_response = response.json()
        
        print(gemini_response)  # Debug pour voir la vraie structure

        if isinstance(gemini_response, dict) and "candidates" in gemini_response:
            candidates = gemini_response["candidates"]
            
            if isinstance(candidates, list) and len(candidates) > 0:
                content = candidates[0].get("content")
                
                # Vérifie si content est une simple string ou un objet
                if isinstance(content, dict) and "parts" in content:
                    text_value = content["parts"][0].get("text", "Réponse vide.")
                elif isinstance(content, str):
                    text_value = content
                else:
                    text_value = "Réponse inattendue."

                response_text = {"parts": [{"text": text_value}]}
            else:
                response_text = {"parts": [{"text": "Aucune réponse valide reçue de l'IA."}]}
        else:
            response_text = {"parts": [{"text": "Réponse inattendue du serveur."}]}

    except Exception as e:
        print(f"Erreur : {e}")  # Debug pour voir l'erreur
        response_text = {"parts": [{"text": "Nous rencontrons actuellement un problème technique. Veuillez réessayer plus tard. Merci pour votre patience !"}]}

    # Retourner la réponse et l'historique
    return jsonify({'gemini_response': response_text, 'history': conversation_history})

if __name__ == '__main__':
    app.run(debug=True)
