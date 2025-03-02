from flask import Flask, request, render_template, jsonify
import requests
from flask_sqlalchemy import SQLAlchemy
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

def parse_vtt(vtt_text):
    """
    Parse le contenu d'un fichier VTT et retourne une liste de cues structurés
    sous forme de dictionnaires avec les clés 'text', 'start' et 'duration'.
    """
    cues = []
    lines = vtt_text.splitlines()
    # Enlever la ligne d'en-tête "WEBVTT" si présente
    if lines and lines[0].startswith("WEBVTT"):
        lines = lines[1:]
    # Supprimer les lignes vides
    lines = [line.strip() for line in lines if line.strip() != ""]
    # Expression régulière pour détecter la ligne de timecode
    timecode_regex = re.compile(r"(\d{2}:\d{2}:\d{2}\.\d{3})\s-->\s(\d{2}:\d{2}:\d{2}\.\d{3})")
    current_time = None
    text_lines = []
    for line in lines:
        match = timecode_regex.match(line)
        if match:
            # S'il existe déjà un timecode en cours, sauvegarder le cue précédent
            if current_time and text_lines:
                h, m, s = map(float, current_time[0].split(':'))
                start = h * 3600 + m * 60 + s
                h2, m2, s2 = map(float, current_time[1].split(':'))
                end = h2 * 3600 + m2 * 60 + s2
                cues.append({"text": " ".join(text_lines), "start": start, "duration": end - start})
            current_time = (match.group(1), match.group(2))
            text_lines = []
        else:
            text_lines.append(line)
    # Sauvegarder le dernier cue s'il existe
    if current_time and text_lines:
        h, m, s = map(float, current_time[0].split(':'))
        start = h * 3600 + m * 60 + s
        h2, m2, s2 = map(float, current_time[1].split(':'))
        end = h2 * 3600 + m2 * 60 + s2
        cues.append({"text": " ".join(text_lines), "start": start, "duration": end - start})
    return cues

def get_transcript(video_id):
    """
    Récupère les sous-titres en anglais et en chinois d'une vidéo YouTube via yt_dlp.
    Si les sous-titres sont trouvés, ils sont convertis à partir du format VTT en une liste de cues,
    sauvegardés en base et retournés.
    """
    transcript_data_en = get_transcript_from_db(video_id, 'en')
    transcript_data_ch = get_transcript_from_db(video_id, 'zh')

    # Si les transcriptions existent déjà en base, les retourner
    if transcript_data_en and transcript_data_ch:
        return {"en": eval(transcript_data_en), "ch": eval(transcript_data_ch)}

    video_url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en', 'zh', 'zh-CN']
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
        
        # Récupérer les sous-titres manuels et automatiques
        subtitles = info.get("subtitles", {})
        auto_captions = info.get("automatic_captions", {})

        # Pour l'anglais, priorité aux sous-titres manuels
        if 'en' in subtitles:
            transcript_en_info = subtitles['en']
        elif 'en' in auto_captions:
            transcript_en_info = auto_captions['en']
        else:
            transcript_en_info = None

        transcript_en_text = ""
        if transcript_en_info and isinstance(transcript_en_info, list):
            # On utilise le premier fichier disponible
            en_url = transcript_en_info[0].get("url")
            if en_url:
                resp = requests.get(en_url)
                if resp.status_code == 200:
                    transcript_en_text = resp.text

        # Pour le chinois, on teste plusieurs variantes (zh et zh-CN)
        if 'zh' in subtitles:
            transcript_ch_info = subtitles['zh']
        elif 'zh-CN' in subtitles:
            transcript_ch_info = subtitles['zh-CN']
        elif 'zh' in auto_captions:
            transcript_ch_info = auto_captions['zh']
        elif 'zh-CN' in auto_captions:
            transcript_ch_info = auto_captions['zh-CN']
        else:
            transcript_ch_info = None

        transcript_ch_text = ""
        if transcript_ch_info and isinstance(transcript_ch_info, list):
            zh_url = transcript_ch_info[0].get("url")
            if zh_url:
                resp = requests.get(zh_url)
                if resp.status_code == 200:
                    transcript_ch_text = resp.text

        # Parser le contenu VTT pour obtenir des cues structurés
        if transcript_en_text:
            transcript_en_cues = parse_vtt(transcript_en_text)
            save_transcript_to_db(video_id, 'en', repr(transcript_en_cues))
        else:
            transcript_en_cues = [{"text": "Aucune transcription anglaise trouvée.", "start": 0, "duration": 0}]

        if transcript_ch_text:
            transcript_ch_cues = parse_vtt(transcript_ch_text)
            save_transcript_to_db(video_id, 'zh', repr(transcript_ch_cues))
        else:
            transcript_ch_cues = [{"text": "Aucune transcription chinoise trouvée.", "start": 0, "duration": 0}]

        return {"en": transcript_en_cues, "ch": transcript_ch_cues}
    except Exception as e:
        return {"error": f"Erreur inattendue : {e}"}

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
                "parts": [{"text": "Context : App de transcription youtube."}]
            },
            {
                "role": "user",
                "parts": [{"text": msg['text']} for msg in conversation_history]
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

    return jsonify({'gemini_response': response_text, 'history': conversation_history})

if __name__ == '__main__':
    app.run(debug=True)
