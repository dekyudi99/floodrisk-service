# import os
from app import create_app

app = create_app()
# app = create_app(os.getenv('FLASK_CONFIG') or 'default')

# Set the request body size limit to 25 Megabytes (example)
# app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024

if __name__ == '__main__':
    app.run(debug=True)

