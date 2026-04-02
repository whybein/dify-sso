from flask import Flask


def init_app(app: Flask):
    # register blueprint routers
    from flask_cors import CORS
    from app.api.router import api
    from app.configs import config

    allowed_origins = [config.CONSOLE_WEB_URL] if config.CONSOLE_WEB_URL else []

    CORS(
        app,
        origins=allowed_origins,
        allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
        methods=["GET", "PUT", "POST", "DELETE", "OPTIONS", "PATCH"],
        expose_headers=["X-Version", "X-Env"],
    )

    app.register_blueprint(api)
