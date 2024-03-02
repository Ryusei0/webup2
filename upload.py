from flask import Flask, request, jsonify
from flask_cors import CORS
import boto3
from werkzeug.utils import secure_filename
from datetime import datetime
import azure.cognitiveservices.speech as speechsdk
from pydub import AudioSegment
import os
import uuid
import tempfile

app = Flask(__name__)
CORS(app)

AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
speech_key = os.environ['AZURE_SPEECH_KEY']
service_region = os.environ['AZURE_SERVICE_REGION']
S3_BUCKET_NAME = "testunity1.0"
AWS_REGION = "ap-northeast-3"
company_id = os.environ['COMPANY_ID']  # 余分なカンマを削除
# 一時ディレクトリの作成
output_directory = tempfile.mkdtemp()

s3 = boto3.client('s3', region_name=AWS_REGION, aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
dynamodb = boto3.resource('dynamodb', region_name='ap-northeast-1')
table = dynamodb.Table('maindatabase')

def text_to_speech(text, text_id):
    # タイトルからファイル名を生成
    sanitized_text = secure_filename(text)
    base_filename = f"{sanitized_text}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}"
    wav_filename = os.path.join(output_directory, f"{base_filename}.wav")
    mp3_filename = os.path.join(output_directory, f"{base_filename}.mp3")
    
    speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=service_region)
    audio_config = speechsdk.audio.AudioOutputConfig(filename=wav_filename)
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    
    ssml_string = f"""
    <speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis"
           xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="ja-JP">
        <voice name="ja-JP-DaichiNeural">
            <mstts:express-as style="customerservice" styledegree="3">
                {text}
            </mstts:express-as>
        </voice>
    </speak>"""

    synthesizer.speak_ssml_async(ssml_string).get()
    
    # WAVからMP3への変換
    audio = AudioSegment.from_wav(wav_filename)
    audio.export(mp3_filename, format="mp3")
    os.remove(wav_filename)  # WAVファイルの削除

    return mp3_filename

@app.route('/upload', methods=['POST'])
def upload_file():
    if request.method == 'POST':
        texts = request.form.getlist('text[]')
        files = request.files.getlist('file[]')
        responses = []

        # ファイルとテキストのペアを処理するための修正
        for text, file in zip(texts, files):
            if file.filename:  # 空のファイル名をチェック
                original_filename = secure_filename(file.filename)
                folder_name = 'uploads/'
                upload_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                full_file_name = os.path.join(folder_name, original_filename)

                s3.upload_fileobj(file, S3_BUCKET_NAME, full_file_name)
                file_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{full_file_name}"
                
                # DynamoDBに保存
                table.put_item(
                    Item={
                        'company_id': os.environ['COMPANY_ID'],
                        'upload_timestamp': upload_timestamp,
                        'text': text,
                        'file_url': file_url
                    }
                )
                
                responses.append({"message": "Upload successful", "file_url": file_url})
            else:
                # ファイルが選択されていない場合の処理
                responses.append({"message": "No file selected"})

        return jsonify(responses)

    return jsonify({"message": "Upload failed"}), 400

@app.route('/list_texts', methods=['GET'])
def list_texts():
    response = table.scan()
    items = response['Items']
    return jsonify(items)

@app.route('/delete', methods=['POST'])
def delete_file():
    upload_timestamp = request.json['upload_timestamp']
    
    # DynamoDBから該当するレコードを取得する際のエラーを修正
    try:
        response = table.get_item(
            Key={
                'company_id': company_id,
                'upload_timestamp': upload_timestamp
            }
        )
        item = response.get('Item', None)
        if not item:
            return jsonify({"message": "Item not found"}), 404
        
        file_url = item['file_url']
        # S3オブジェクトのキーを抽出
        file_key = file_url.split(f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/")[1]

        # S3からファイルを削除
        s3.delete_object(Bucket=S3_BUCKET_NAME, Key=file_key)

        # DynamoDBからレコードを削除
        table.delete_item(
            Key={
                'company_id': company_id,
                'upload_timestamp': upload_timestamp
            }
        )
        
        return jsonify({"message": "Delete successful"})
    except Exception as e:
        return jsonify({"message": "Error deleting item", "error": str(e)}), 500
    

@app.route('/upload_extended', methods=['POST'])
def upload_extended():
    # リクエストから複数の入力を受け取る
    texts = request.form.getlist('text[]')
    descriptions = request.form.getlist('description[]')
    thumbnails = request.files.getlist('thumbnail[]')
    medias = request.files.getlist('media[]')
    responses = []

    for text, description, thumbnail, media in zip(texts, descriptions, thumbnails, medias):
        upload_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        text_id = str(uuid.uuid4())  # 各アップロードセットに一意のIDを生成

        # 音声の生成とアップロード
        if text and description:
            mp3_filename = text_to_speech(description, text_id)
            audio_file_key = f'subuploads/{text_id}/audio/{os.path.basename(mp3_filename)}'
            s3.upload_file(mp3_filename, S3_BUCKET_NAME, audio_file_key)
            audio_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{audio_file_key}"
        else:
            audio_url = None

        # サムネイルのアップロード
        if thumbnail and thumbnail.filename:
            thumbnail_filename = secure_filename(thumbnail.filename)
            thumbnail_filepath = f'subuploads/{text_id}/thumbnail/{thumbnail_filename}'
            s3.upload_fileobj(thumbnail, S3_BUCKET_NAME, thumbnail_filepath)
            thumbnail_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{thumbnail_filepath}"
        else:
            thumbnail_url = None

        # メディアのアップロード
        if media and media.filename:
            media_filename = secure_filename(media.filename)
            media_filepath = f'subuploads/{text_id}/media/{media_filename}'
            s3.upload_fileobj(media, S3_BUCKET_NAME, media_filepath)
            media_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{media_filepath}"
        else:
            media_url = None

        # DynamoDBに保存
        dynamodb_record = {
            'company_id': os.environ['COMPANY_ID'],
            'text_id': text_id,
            'upload_timestamp': upload_timestamp,
            'text': text,
            'description': description,
            'audio_url': audio_url,
            'thumbnail_url': thumbnail_url,
            'media_url': media_url
        }
        table.put_item(Item=dynamodb_record)

        responses.append({"message": "Upload successful", "data": dynamodb_record})

    return jsonify(responses)

if __name__ == "__main__":
    app.run(debug=True)
