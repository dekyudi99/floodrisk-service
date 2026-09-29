import os
# pyrefly: ignore [missing-import]
from flask import Flask
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    
    origins = ["http://localhost:5174", "http://localhost:5173", "https://flowgis.ikya.my.id", "https://api-flowgis.ikya.my.id"]

    CORS(app, resources={r"/api/*": {"origins": origins}})

    from app.routes import api_bp
    app.register_blueprint(api_bp)

    return app