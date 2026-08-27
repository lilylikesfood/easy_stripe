from waitress import serve
from app import create_app

app = create_app()


if __name__ == "__main__":
    # app.run(debug=False, use_reloader=False, host="0.0.0.0", port=5000)
    # `0.0.0.0` tells Flask to accept connections from any device on the network

    # Waitress is a production-ready WSGI server (unlike Flask's built-in dev server, which prints the "do not use in production" warning).
    # `host="0.0.0.0"` still means: accept connections from any device on the network, not just this machine
    serve(app, host="0.0.0.0", port=5000)