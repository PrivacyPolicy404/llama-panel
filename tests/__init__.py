import os

# Los tests esperan los puertos de siempre aunque el paths.env de la máquina los cambie (el entorno tiene prioridad)
os.environ["LLAMA_CHAT_PORT"] = "8080"
os.environ["LLAMA_IMG_PORT"] = "5001"
