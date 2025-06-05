# health_checker.py
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

HEALTH_CHECK_PORT = int(os.environ.get('PORT', 8080))

class HealthCheckHandler(BaseHTTPRequestHandler):
    """
    Обработчик HTTP-запросов для health check.
    Отвечает 200 OK на GET и HEAD запросы к /healthz.
    """
    def do_GET(self):
        """Обрабатывает GET-запросы."""
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/healthz':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"OK") # Отправляем тело ответа
        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_HEAD(self):
        """
        Обрабатывает HEAD-запросы.
        Логика та же, что и у GET, но тело ответа не отправляется.
        Именно это и нужно для UptimeRobot.
        """
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/healthz':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers() # Заголовки отправили, тело - нет.
        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()

    def log_message(self, format, *args):
        """Подавляем стандартное логирование запросов."""
        return

def _run_server():
    """Внутренняя функция для запуска HTTP-сервера."""
    try:
        server_address = ('', HEALTH_CHECK_PORT)
        httpd = HTTPServer(server_address, HealthCheckHandler)
        logger.info(f"Health Check HTTP-сервер запущен и слушает порт {HEALTH_CHECK_PORT}")
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"Критическая ошибка в Health Check HTTP-сервере: {e}", exc_info=True)

def start_health_check_server_in_thread():
    """Запускает HTTP-сервер для health check в отдельном фоновом потоке."""
    health_thread = threading.Thread(target=_run_server, daemon=True)
    health_thread.start()
    logger.info("Поток для Health Check HTTP-сервера инициирован.")
    return health_thread

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger.info("Запуск health_checker.py напрямую для теста...")
    _run_server()
