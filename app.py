"""FocusLog デスクトップアプリ版。

ブラウザ不要のネイティブウィンドウで FocusLog を開きます。
内部では 127.0.0.1 の空きポートでサーバーを起動しますが、
外部ネットワークには公開されません。

Run: python app.py
"""
from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer

import webview  # pip install pywebview

import server


def main():
    server.init_db()
    # ポート0 = OSが空きポートを割り当てる（他アプリと衝突しない）
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    webview.create_window(
        "FocusLog",
        f"http://127.0.0.1:{port}",
        width=1280,
        height=860,
        min_size=(480, 600),
    )
    try:
        webview.start()  # ウィンドウを閉じるまでここで待機
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
