from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
import os
import re
import secrets

app = FastAPI()
security = HTTPBasic()
templates = Jinja2Templates(directory="templates")

# Diretório base
BASE_DIR = "/emby_media" 
CATEGORIES = ["desenhos", "filmes", "novelas", "series", "anime", "dorama", "filmes-desenho", "rellshort"]

current_config = {
    "category": "filmes",
    "subfolder": ""
}

ADMIN_USERNAME = os.getenv("WEB_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("WEB_PASSWORD", "admin")

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

class WebhookPayload(BaseModel):
    file_name: str
    file_size: int
    mime_type: str
    stream_link: str

# Payload para as novas ações de arquivos
class FileActionPayload(BaseModel):
    category: str
    subfolder: str
    file_name: str
    new_name: str = None

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, username: str = Depends(verify_credentials)):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "categories": CATEGORIES,
        "current_config": current_config
    })

@app.get("/subfolders/{category}", dependencies=[Depends(verify_credentials)])
async def get_subfolders(category: str):
    category_path = os.path.join(BASE_DIR, category)
    if not os.path.exists(category_path):
        return {"subfolders": []}
    
    subfolders = [f for f in os.listdir(category_path) if os.path.isdir(os.path.join(category_path, f))]
    subfolders.sort()
    return {"subfolders": subfolders}

@app.post("/set-target", dependencies=[Depends(verify_credentials)])
async def set_target(
    category: str = Form(...), 
    subfolder_select: str = Form(""), 
    new_subfolder: str = Form("")
):
    if subfolder_select == "NEW":
        final_subfolder = new_subfolder.strip()
    else:
        final_subfolder = subfolder_select.strip()

    if final_subfolder:
        target_dir = os.path.join(BASE_DIR, category, final_subfolder)
        os.makedirs(target_dir, exist_ok=True)

    current_config["category"] = category
    current_config["subfolder"] = final_subfolder

    return {
        "status": "success", 
        "message": f"Destino atualizado para: {category}/{final_subfolder}" if final_subfolder else f"Destino atualizado para: {category} (Raiz)"
    }

# --- NOVAS ROTAS PARA GERENCIAR ARQUIVOS ---

@app.get("/list-files", dependencies=[Depends(verify_credentials)])
async def list_files(category: str, subfolder: str = ""):
    target_dir = os.path.join(BASE_DIR, category, subfolder)
    if not os.path.exists(target_dir):
        return {"files": []}
    
    # FILTRO: Lista apenas os arquivos que terminam com .strm (ignora pastas e outros arquivos)
    files = [f for f in os.listdir(target_dir) if os.path.isfile(os.path.join(target_dir, f)) and f.endswith('.strm')]
    files.sort()
    return {"files": files}

@app.get("/recent-files", dependencies=[Depends(verify_credentials)])
async def get_recent_files(limit: int = 15):
    recent_files = []
    
    # Percorre todas as categorias configuradas
    for category in CATEGORIES:
        cat_path = os.path.join(BASE_DIR, category)
        if not os.path.exists(cat_path):
            continue
            
        # Vasculha a categoria e suas subpastas
        for root, dirs, files in os.walk(cat_path):
            for file in files:
                if file.endswith('.strm'):
                    full_path = os.path.join(root, file)
                    
                    # Descobre o nome da subpasta (se houver)
                    rel_path = os.path.relpath(root, cat_path)
                    subfolder = "" if rel_path == "." else rel_path
                    
                    # Pega a data de modificação do arquivo
                    mtime = os.path.getmtime(full_path)
                    recent_files.append({
                        "file_name": file,
                        "category": category,
                        "subfolder": subfolder.replace('\\', '/'), # Garante o formato correto
                        "mtime": mtime
                    })
    
    # Ordena dos mais recentes para os mais antigos e pega o limite
    recent_files.sort(key=lambda x: x["mtime"], reverse=True)
    return {"files": recent_files[:limit]}

@app.post("/rename-file", dependencies=[Depends(verify_credentials)])
async def rename_file(payload: FileActionPayload):
    if not payload.new_name:
        return {"status": "error", "message": "O novo nome não pode ficar em branco."}
    
    # FORÇA A EXTENSÃO .STRM
    clean_new_name = payload.new_name.strip()
    if not clean_new_name.endswith('.strm'):
        clean_new_name += '.strm'
    
    target_dir = os.path.join(BASE_DIR, payload.category, payload.subfolder)
    old_path = os.path.join(target_dir, payload.file_name)
    new_path = os.path.join(target_dir, clean_new_name)
    
    if not os.path.exists(old_path):
        return {"status": "error", "message": "O arquivo original não foi encontrado."}
        
    try:
        os.rename(old_path, new_path)
        return {"status": "success", "message": f"Arquivo renomeado para '{clean_new_name}' com sucesso!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/delete-file", dependencies=[Depends(verify_credentials)])
async def delete_file(payload: FileActionPayload):
    target_dir = os.path.join(BASE_DIR, payload.category, payload.subfolder)
    target_path = os.path.join(target_dir, payload.file_name)
    
    if not os.path.exists(target_path):
        return {"status": "error", "message": "O arquivo não foi encontrado."}
        
    try:
        os.remove(target_path)
        return {"status": "success", "message": "Arquivo excluído com sucesso!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- WEBHOOK (MANTIDO LIVRE PARA RECEBER O POST) ---

@app.post("/webhook")
async def receive_webhook(payload: WebhookPayload):
    clean_name = re.sub(r'\s*@\w+', '', payload.file_name).strip()
    
    if not clean_name.endswith('.strm'):
        clean_name += '.strm'
    
    internal_link = payload.stream_link.replace("https://fsb.kabaweb.in", "http://fsb-go:8080")

    target_dir = os.path.join(BASE_DIR, current_config["category"])
    if current_config["subfolder"]:
        target_dir = os.path.join(target_dir, current_config["subfolder"])
    
    os.makedirs(target_dir, exist_ok=True)
    file_path = os.path.join(target_dir, clean_name)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(internal_link)

    return {"status": "created", "path": file_path, "link": internal_link}