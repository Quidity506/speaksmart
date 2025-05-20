# health_checker.py
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

logger = logging.getLogger(__name__) # Используем логгер с именем этого модуля

# Порт для Health Check сервера (Render предоставляет его через переменную PORT)
# Используем 8080 по умолчанию, если PORT не задан, но Render должен предоставить переменную PORT.
HEALTH_CHECK_PORT = int(os.environ.get('PORT', 8080))

class HealthCheckHandler(BaseHTTPRequestHandler):
    """
    Обработчик HTTP-запросов для health check.
    Отвечает 200 OK на GET /healthz.
    """
    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/healthz':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"OK")
            # Логируем успешный health check, если нужно (можно закомментировать для уменьшения логов)
            # logger.debug(f"Health check OK on port {HEALTH_CHECK_PORT}")
        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Not Found")

    def log_message(self, format, *args):
        """
        Подавляем стандартное логирование запросов от BaseHTTPRequestHandler,
        чтобы не засорять логи Render сообщениями о GET /healthz.
        Если хотите видеть эти логи, закомментируйте этот метод.
        """
        # logger.debug("%s - %s" % (self.address_string(), format % args))
        return


def _run_server():
    """Внутренняя функция для запуска HTTP-сервера."""
    try:
        server_address = ('', HEALTH_CHECK_PORT) # Слушаем на всех интерфейсах
        httpd = HTTPServer(server_address, HealthCheckHandler)
        logger.info(f"Health Check HTTP-сервер запущен и слушает порт {HEALTH_CHECK_PORT}")
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"Критическая ошибка в Health Check HTTP-сервере: {e}", exc_info=True)

def start_health_check_server_in_thread():
    """
    Запускает HTTP-сервер для health check в отдельном фоновом потоке.
    """
    health_thread = threading.Thread(target=_run_server, daemon=True)
    health_thread.start()
    logger.info("Поток для Health Check HTTP-сервера инициирован.")
    return health_thread

if __name__ == '__main__':
    # Этот блок выполнится, если запустить health_checker.py напрямую
    # (например, для локального теста только HTTP-сервера)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger.info("Запуск health_checker.py напрямую для теста...")
    # start_health_check_server_in_thread() # Можно так
    _run_server() # Или так, чтобы основной поток не завершался сразу
    # Для реального использования в speaksmart.py будет вызываться start_health_check_server_in_thread()
