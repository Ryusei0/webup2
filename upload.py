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
company_id = os.environ['COMPANY_ID']
output_directory = tempfile.mkdtemp()

s3 = boto3.client('s3', region_name=AWS_REGION, aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
dynamodb = boto3.resource('dynamodb', region_name='ap-northeast-1')
table = dynamodb.Table('maindatabase')

def generate_unique_filename(original_filename):
    extension = original_filename.rsplit('.', 1)[1] if '.' in original_filename else ''
    unique_filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}.{extension}"
    return unique_filename

def text_to_speech(text):
    sanitized_text = secure_filename(text)
    base_filename = f"{sanitized_text}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}"
    wav_filename = os.path.join(output_directory, f"{base_filename}.wav")
    mp3_filename = os.path.join(output_directory, f"{base_filename}.mp3")
    
    speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=service_region)
    audio_config = speechsdk.audio.AudioOutputConfig(filename=wav_filename)
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    
    ssml_string = f"""<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis"
           xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="ja-JP">
        <voice name="ja-JP-DaichiNeural">
            <mstts:express-as style="customerservice" styledegree="3">
                {text}
            </mstts:express-as>
        </voice>
    </speak>"""
    synthesizer.speak_ssml_async(ssml_string).get()
    
    audio = AudioSegment.from_wav(wav_filename)
    audio.export(mp3_filename, format="mp3")
    os.remove(wav_filename)  # Ensure the wav file is removed after conversion

    return mp3_filename

@app.route('/upload_extended', methods=['POST'])
def upload_extended():
    try:
        texts = request.form.getlist('text[]')
        descriptions = request.form.getlist('description[]')
        thumbnails = request.files.getlist('thumbnail[]')
        medias = request.files.getlist('media[]')
        responses = []

        for text, description, thumbnail, media in zip(texts, descriptions, thumbnails, medias):
            if not text or not description:
                continue  # Skip if text or description is missing
            
            upload_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            text_id = str(uuid.uuid4())  # Generate a unique ID for each upload set

            # Process and upload audio
            audio_url = None
            try:
                mp3_filename = text_to_speech(description)
                audio_file_key = f'subuploads/{text_id}/audio/{os.path.basename(mp3_filename)}'
                s3.upload_file(mp3_filename, S3_BUCKET_NAME, audio_file_key)
                audio_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{audio_file_key}"
            except Exception as e:
                app.logger.error(f"Error uploading audio file: {str(e)}")
            finally:
                if os.path.exists(mp3_filename):
                    os.remove(mp3_filename)  # Ensure the mp3 file is removed after upload

            # Upload thumbnail
            thumbnail_url = None
            if thumbnail and thumbnail.filename:
                try:
                    thumbnail_filename = generate_unique_filename(thumbnail.filename)
                    thumbnail_filepath = f'subuploads/{text_id}/thumbnail/{thumbnail_filename}'
                    s3.upload_fileobj(thumbnail, S3_BUCKET_NAME, thumbnail_filepath)
                    thumbnail_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{thumbnail_filepath}"
                except Exception as e:
                    app.logger.error(f"Error uploading thumbnail: {str(e)}")

            # Upload media
            media_url = None
            if media and media.filename:
                try:
                    media_filename = generate_unique_filename(media.filename)
                    media_filepath = f'subuploads/{text_id}/media/{media_filename}'
                    s3.upload_fileobj(media, S3_BUCKET_NAME, media_filepath)
                    media_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{media_filepath}"
                except Exception as e:
                    app.logger.error(f"Error uploading media: {str(e)}")

            # Save to DynamoDB
            try:
                dynamodb_record = {
                    'company_id': company_id,
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
            except Exception as e:
                app.logger.error(f"Error saving to DynamoDB: {str(e)}")
                responses.append({"message": "Error processing the upload", "error": str(e)})

        if not responses:
            return jsonify({"message": "No valid data provided"}), 400

        return jsonify(responses)
    except Exception as e:
        app.logger.error(f"An error occurred: {str(e)}")
        return jsonify({"message": "Error processing the upload", "error": str(e)}), 500

@app.route('/upload', methods=['POST'])
def upload_file():
    if request.method == 'POST':
        texts = request.form.getlist('text[]')
        files = request.files.getlist('file[]')
        responses = []

        for text, file in zip(texts, files):
            if file.filename:
                unique_filename = generate_unique_filename(file.filename)
                folder_name = 'uploads/'
                full_file_name = os.path.join(folder_name, unique_filename)

                s3.upload_fileobj(file, S3_BUCKET_NAME, full_file_name)
                file_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{full_file_name}"
                
                upload_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                table.put_item(
                    Item={
                        'company_id': company_id,
                        'upload_timestamp': upload_timestamp,
                        'text': text,
                        'file_url': file_url
                    }
                )
                
                responses.append({"message": "Upload successful", "file_url": file_url})
            else:
                responses.append({"message": "No file selected"})

        return jsonify(responses)

    return jsonify({"message": "Upload failed"}), 400

@app.route('/list_texts', methods=['GET'])
def list_texts():
    response = table.scan()
    items = response['Items']
    return jsonify(items)

@app.route('/list_extended_uploads', methods=['GET'])
def list_extended_uploads():
    # テーブルから全てのアイテムを取得するためにスキャン操作を実行
    response = table.scan()
    items = response['Items']
    # 'upload_extended'関数によって作成されたアップロードだけを含むようにアイテムをフィルタリング
    extended_uploads = [item for item in items if 'text_id' in item]
    return jsonify(extended_uploads)

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
    
@app.route('/delete_subupload', methods=['POST'])
def delete_subupload():
    text_id = request.json['text_id']

    try:
        # DynamoDBから該当するレコードを検索
        response = table.get_item(
            Key={
                'company_id': company_id,
                'text_id': text_id
            }
        )
        item = response.get('Item', None)
        if not item:
            return jsonify({"message": "Item not found"}), 404

        # S3から関連ファイルを削除する
        if item.get('audio_url'):
            audio_key = item['audio_url'].split(f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/")[1]
            s3.delete_object(Bucket=S3_BUCKET_NAME, Key=audio_key)
        
        if item.get('thumbnail_url'):
            thumbnail_key = item['thumbnail_url'].split(f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/")[1]
            s3.delete_object(Bucket=S3_BUCKET_NAME, Key=thumbnail_key)

        if item.get('media_url'):
            media_key = item['media_url'].split(f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/")[1]
            s3.delete_object(Bucket=S3_BUCKET_NAME, Key=media_key)

        # DynamoDBからレコードを削除
        table.delete_item(
            Key={
                'company_id': company_id,
                'text_id': text_id
            }
        )
        
        return jsonify({"message": "Delete successful"})
    except Exception as e:
        return jsonify({"message": "Error deleting item", "error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
