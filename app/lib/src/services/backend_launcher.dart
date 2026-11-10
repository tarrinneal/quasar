/// Cross-platform launcher hook: auto-starts `server.py` on Desktop when needed,
/// and resolves the default API base URL on both Web and Desktop.
library;

export 'backend_launcher_stub.dart'
    if (dart.library.io) 'backend_launcher_io.dart';
