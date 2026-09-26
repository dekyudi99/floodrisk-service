import os
from flask import Flask
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    
    default_origins = ["http://localhost:5174", "http://localhost:5173", "https://flowgis.ikya.my.id", "https://api-flowgis.ikya.my.id"]
    env_cors = os.getenv("CORS_ORIGINS")
    origins = [orig.strip() for orig in env_cors.split(",") if orig.strip()] if env_cors else default_origins

    CORS(app, resources={r"/api/*": {"origins": origins}})

    from app.routes import api_bp
    app.register_blueprint(api_bp)

    return app