String resolveDefaultApiBaseUrl() {
  final Uri base = Uri.base;
  final String? override = base.queryParameters['api'];
  if (override != null && override.trim().isNotEmpty) {
    return override.trim().replaceAll(RegExp(r'/+$'), '');
  }
  if (base.scheme == 'http' || base.scheme == 'https') {
    // When served directly by server.py (e.g. port 49341), use same-origin.
    // When running via `flutter run -d chrome` on a random dev port, connect to 127.0.0.1:49341.
    if (base.port == 49341 || base.path.startsWith('/flutter')) {
      return '${base.scheme}://${base.host}${base.hasPort ? ':${base.port}' : ''}';
    }
  }
  return 'http://127.0.0.1:49341';
}

Future<bool> ensureLocalBackendRunning(String baseUrl) async {
  // In a browser, the backend is started via `quasar` CLI (or already running).
  return false;
}
