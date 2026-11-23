import json
import logging
import mimetypes
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .codec import encode
from .errors import DomainError
from .routes import dispatch

PUBLIC = Path(__file__).resolve().parent.parent / 'web'


class WebHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(12)

    def do_GET(self):
        self.serve('GET')

    def do_POST(self):
        self.serve('POST')

    def serve(self, method):
        try:
            path = urlsplit(self.path).path
            if method == 'GET' and not path.startswith('/api/'):
                return self.static(path)
            body = self.read_json() if method == 'POST' else None
            result = dispatch(self.server.database, method, path, body)
            if isinstance(result, tuple):
                value, media, name = result
                self.send_data(value if isinstance(value, str) else encode(value), media, attachment=name)
            else:
                self.send_data(encode(result), 'application/json; charset=utf-8')
        except DomainError as exc:
            self.send_data(encode({'error': exc.code, 'message': str(exc)}), 'application/json; charset=utf-8', exc.status)
        except sqlite3.OperationalError:
            logging.exception('存储操作失败')
            self.send_data('{"message":"存储暂不可用"}', 'application/json; charset=utf-8', 503)
        except (TimeoutError, ConnectionError):
            self.close_connection = True
        except Exception:
            logging.exception('请求处理失败')
            self.send_data('{"message":"请求处理失败"}', 'application/json; charset=utf-8', 500)

    def read_json(self):
        if self.headers.get_content_type() != 'application/json':
            raise DomainError('content_type', '请求需要 JSON 正文', 415)
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            size = 0
        if not 1 <= size <= 1600000:
            raise DomainError('request_size', 'JSON 正文为空或超过大小限制', 413)
        try:
            return json.loads(self.rfile.read(size).decode('utf-8'))
        except (ValueError, RecursionError) as exc:
            raise DomainError('json', 'JSON 正文格式不正确', 400) from exc

    def static(self, path):
        target = (PUBLIC / (path.lstrip('/') or 'index.html')).resolve()
        if not target.is_relative_to(PUBLIC) or target.suffix not in {'.html', '.js', '.css'} or not target.is_file():
            raise DomainError('page_missing', '找不到此文件', 404)
        media = {'.js': 'text/javascript'}.get(target.suffix) or mimetypes.guess_type(target.name)[0]
        self.send_data(target.read_bytes(), media + '; charset=utf-8')

    def send_data(self, content, media, status=200, attachment=None):
        data = content.encode('utf-8') if isinstance(content, str) else content
        self.send_response(status)
        self.send_header('Content-Type', media)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if attachment:
            self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(data)


def make_server(database, port):
    server = ThreadingHTTPServer(('127.0.0.1', port), WebHandler)
    server.database = database
    return server
