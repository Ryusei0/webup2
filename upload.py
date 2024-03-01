from flask import Flask, request, jsonify
from flask_cors import CORS
import boto3
from werkzeug.utils import secure_filename
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
S3_BUCKET_NAME = "testunity1.0"
AWS_REGION = "ap-northeast-3"

s3 = boto3.client('s3', region_name=AWS_REGION, aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
dynamodb = boto3.resource('dynamodb', region_name='ap-northeast-1')
table = dynamodb.Table('maindatabase')

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
    company_id = os.environ['COMPANY_ID'],
    upload_timestamp = request.json['upload_timestamp']
    
    # DynamoDBから該当するレコードを取得
    response = table.get_item(
        Key={
            'company_id': company_id,
            'upload_timestamp': upload_timestamp
        }
    )
    item = response['Item']
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

if __name__ == '__main__':
    app.run(debug=True)
