import json
import os
import io
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

def get_gdrive_service(raw_token: str):
    """传入从 st.secrets 获取的 token 字符串，进行鉴权"""
    try:
        token_dict = json.loads(raw_token, strict=False)
        creds = Credentials.from_authorized_user_info(token_dict)
        return build('drive', 'v3', credentials=creds), None
    except Exception as e:
        return None, f"Token解析失败: {str(e)}"

def upload_to_gdrive(drive_service, local_file_path, file_name, folder_id, mime_type='application/pdf'):
    try:
        file_metadata = {'name': file_name, 'parents': [folder_id]}
        media = MediaFileUpload(local_file_path, mimetype=mime_type)
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return True, file.get('id')
    except Exception as e:
        return False, f"上传异常: {str(e)[:50]}"

# ==========================================
# 新增：云端历史账本永久固化模块
# ==========================================
def get_cloud_history(drive_service, folder_id, file_name="download_history.json"):
    """从 Google Drive 读取历史账本进内存"""
    if not drive_service or not folder_id:
        return {}, None
        
    # 在指定文件夹中寻找账本文件
    query = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
    try:
        results = drive_service.files().list(q=query, fields="files(id, name)").execute()
        files = results.get('files', [])
        
        if not files:
            return {}, None # 云端还没有账本文件
            
        # 找到账本，下载数据
        file_id = files[0]['id']
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
            
        history_data = json.loads(fh.getvalue().decode('utf-8'))
        return history_data, file_id
    except Exception as e:
        print(f"云端账本读取失败: {e}")
        return {}, None

def update_cloud_history(drive_service, folder_id, history_data, file_name="download_history.json", file_id=None):
    """将更新后的账本覆盖保存到 Google Drive（绝不产生同名文件）"""
    if not drive_service or not folder_id:
        return file_id
        
    temp_path = f"temp_{file_name}"
    # 将字典转为本地临时 JSON 文件
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(history_data, f, ensure_ascii=False, indent=4)
        
    media = MediaFileUpload(temp_path, mimetype='application/json', resumable=True)
    
    try:
        if file_id:
            # 核心逻辑：已有文件ID，直接 update 覆盖内容
            drive_service.files().update(fileId=file_id, media_body=media).execute()
        else:
            # 云端无此文件，create 新建
            file_metadata = {'name': file_name, 'parents': [folder_id]}
            file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            file_id = file.get('id')
    except Exception as e:
        print(f"云端账本覆盖失败: {e}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path) # 用完即焚临时文件
            
    return file_id
