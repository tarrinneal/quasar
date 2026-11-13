import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'src/models/quasar_models.dart';
import 'src/services/backend_launcher.dart';
import 'src/services/quasar_api_client.dart';
import 'src/widgets/diff_viewer.dart';
import 'src/widgets/study_guide_view.dart';
import 'src/widgets/thread_card.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const QuasarStandaloneApp());
}

class QuasarStandaloneApp extends StatelessWidget {
  const QuasarStandaloneApp({super.key, this.apiClient});

  final QuasarApiClient? apiClient;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Quasar — Code Review & Study Guide',
      debugShowCheckedModeBanner: false,
      theme: ThemeData.dark(useMaterial3: true).copyWith(
        scaffoldBackgroundColor: const Color(0xFF0D1117),
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFF58A6FF),
          secondary: Color(0xFF3FB950),
          surface: Color(0xFF161B22),
        ),
        dividerColor: const Color(0xFF21262D),
      ),
      home: SelectionArea(child: QuasarHomeShell(apiClient: apiClient)),
    );
  }
}

enum _MainTab { studyGuide, diff, comments }

class QuasarHomeShell extends StatefulWidget {
  const QuasarHomeShell({super.key, this.apiClient});

  final QuasarApiClient? apiClient;

  @override
  State<QuasarHomeShell> createState() => _QuasarHomeShellState();
}

class _QuasarHomeShellState extends State<QuasarHomeShell> {
  late final QuasarApiClient _api;
  QuasarStatePayload? _state;
  String? _selectedSessionId;
  String? _connectionError;
  bool _loading = true;
  _MainTab _activeTab = _MainTab.studyGuide;
  DiffScrollRequest? _diffScrollReq;
  int _scrollNonce = 0;
  Timer? _pollTimer;

  @override
  void initState() {
    super.initState();
    _api = widget.apiClient ??
        QuasarApiClient(baseUrl: resolveDefaultApiBaseUrl());
    _boot();
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  Future<void> _boot() async {
    if (widget.apiClient == null) {
      await ensureLocalBackendRunning(_api.baseUrl);
    }
    await _refreshState();
    if (widget.apiClient == null) {
      _pollTimer = Timer.periodic(
        const Duration(milliseconds: 2200),
        (_) => _refreshState(silent: true),
      );
    }
  }

  Future<void> _refreshState({bool silent = false}) async {
    if (!silent && mounted) {
      setState(() => _loading = true);
    }
    try {
      final QuasarStatePayload next =
          await _api.fetchState(sessionId: _selectedSessionId);
      if (!mounted) {
        return;
      }
      setState(() {
        _state = next;
        _connectionError = null;
        _loading = false;
        if (_selectedSessionId == null && next.activeSession != null) {
          _selectedSessionId = next.activeSession!.sessionId;
          if (next.activeSession!.commentsOnly) {
            _activeTab = _MainTab.diff;
          }
        }
      });
    } catch (e) {
      if (!mounted) {
        return;
      }
      setState(() {
        _connectionError = '$e';
        _loading = false;
      });
    }
  }

  void _showSnack(String msg, {bool isError = false}) {
    if (!mounted) {
      return;
    }
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(msg),
        backgroundColor:
            isError ? const Color(0xFF8E2A2A) : const Color(0xFF1F6FEB),
        duration: const Duration(seconds: 3),
      ),
    );
  }

  void _jumpToDiff({
    required String filePath,
    required int line,
    String? threadId,
  }) {
    setState(() {
      _activeTab = _MainTab.diff;
      _diffScrollReq = DiffScrollRequest(
        nonce: ++_scrollNonce,
        filePath: filePath,
        line: line,
        threadId: threadId,
      );
    });
  }

  Future<void> _handleJumpToStop(TourStop stop) async {
    final ReviewSession? sess = _state?.activeSession;
    _jumpToDiff(filePath: stop.path, line: stop.startLine);
    if (sess != null && !stop.read && stop.id.isNotEmpty) {
      try {
        await _api.toggleStopRead(
          sessionId: sess.sessionId,
          stopId: stop.id,
        );
        await _refreshState(silent: true);
      } catch (_) {}
    }
  }

  Future<void> _handleJumpToThread(ReviewThread t) async {
    if (t.path.isNotEmpty) {
      _jumpToDiff(
        filePath: t.path,
        line: t.startLine,
        threadId: t.id,
      );
    } else {
      setState(() => _activeTab = _MainTab.comments);
    }
  }

  Future<void> _showStartReviewDialog() async {
    final TextEditingController prCtrl = TextEditingController();
    final TextEditingController repoCtrl = TextEditingController(
      text: _state?.activeSession?.repoDir.isNotEmpty == true
          ? _state!.activeSession!.repoDir
          : (_state?.defaultWorkspaceDir ?? ''),
    );
    final TextEditingController branchCtrl = TextEditingController();
    bool isPrMode = true;
    String effort = 'high';
    bool commentsOnly = false;

    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext ctx) => StatefulBuilder(
        builder: (BuildContext ctx, StateSetter setModalState) => AlertDialog(
          backgroundColor: const Color(0xFF161B22),
          title: const Text(
            '🚀 Start New Code Review (Antigravity CLI)',
            style: TextStyle(fontSize: 16, color: Color(0xFFE6EDF3)),
          ),
          content: SizedBox(
            width: 540,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                SegmentedButton<bool>(
                  segments: const <ButtonSegment<bool>>[
                    ButtonSegment<bool>(
                      value: true,
                      label: Text('GitHub PR URL / #'),
                    ),
                    ButtonSegment<bool>(
                      value: false,
                      label: Text('Local Git Branch'),
                    ),
                  ],
                  selected: <bool>{isPrMode},
                  onSelectionChanged: (Set<bool> v) =>
                      setModalState(() => isPrMode = v.first),
                ),
                const SizedBox(height: 14),
                if (isPrMode)
                  TextField(
                    controller: prCtrl,
                    autofocus: true,
                    decoration: const InputDecoration(
                      labelText: 'GitHub PR URL (e.g. https://github.com/org/repo/pull/123)',
                      border: OutlineInputBorder(),
                    ),
                  )
                else
                  TextField(
                    controller: branchCtrl,
                    decoration: const InputDecoration(
                      labelText: 'Branch name (leave blank for current HEAD + working tree)',
                      border: OutlineInputBorder(),
                    ),
                  ),
                const SizedBox(height: 12),
                TextField(
                  controller: repoCtrl,
                  decoration: const InputDecoration(
                    labelText: 'Local Repository Path (for context & applying fixes)',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 14),
                Row(
                  children: <Widget>[
                    const Text(
                      'Effort Level: ',
                      style: TextStyle(fontSize: 12.5, color: Color(0xFFC9D1D9)),
                    ),
                    const SizedBox(width: 8),
                    DropdownButton<String>(
                      value: effort,
                      dropdownColor: const Color(0xFF21262D),
                      items: const <DropdownMenuItem<String>>[
                        DropdownMenuItem<String>(
                          value: 'low',
                          child: Text('⚡ Low (Fast Single-Pass)'),
                        ),
                        DropdownMenuItem<String>(
                          value: 'medium',
                          child: Text('⚖️ Medium (Balanced)'),
                        ),
                        DropdownMenuItem<String>(
                          value: 'high',
                          child: Text('🔬 High (Deep Multi-Lens — Default)'),
                        ),
                      ],
                      onChanged: (String? v) {
                        if (v != null) {
                          setModalState(() => effort = v);
                        }
                      },
                    ),
                  ],
                ),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  value: commentsOnly,
                  onChanged: (bool? v) =>
                      setModalState(() => commentsOnly = v ?? false),
                  title: const Text(
                    '💬 Comments-Only Mode (skip Study Guide & Reading Tour)',
                    style: TextStyle(fontSize: 12.5),
                  ),
                ),
              ],
            ),
          ),
          actions: <Widget>[
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('Cancel'),
            ),
            FilledButton.icon(
              onPressed: () => Navigator.of(ctx).pop(true),
              icon: const Icon(Icons.play_arrow, size: 16),
              label: const Text('Run Review with agy'),
            ),
          ],
        ),
      ),
    );

    if (confirmed == true) {
      try {
        await _api.startReview(
          prUrl: isPrMode ? prCtrl.text.trim() : '',
          repoDir: repoCtrl.text.trim(),
          branch: isPrMode ? '' : branchCtrl.text.trim(),
          effort: effort,
          commentsOnly: commentsOnly,
        );
        _showSnack('🚀 Dispatched review to Antigravity CLI (`agy`)!');
        await _refreshState(silent: true);
      } catch (e) {
        _showSnack('Failed to start review: $e', isError: true);
      }
    }
    prCtrl.dispose();
    repoCtrl.dispose();
    branchCtrl.dispose();
  }

  Future<void> _showPublishDialog(ReviewSession sess) async {
    String event = 'PENDING';
    final TextEditingController summaryCtrl =
        TextEditingController(text: sess.topLevelComment);

    final bool? go = await showDialog<bool>(
      context: context,
      builder: (BuildContext ctx) => StatefulBuilder(
        builder: (BuildContext ctx, StateSetter setModalState) => AlertDialog(
          backgroundColor: const Color(0xFF161B22),
          title: const Text(
            '☁️ Publish Staged Comments to GitHub',
            style: TextStyle(fontSize: 16, color: Color(0xFFE6EDF3)),
          ),
          content: SizedBox(
            width: 520,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                DropdownButtonFormField<String>(
                  initialValue: event,
                  dropdownColor: const Color(0xFF21262D),
                  decoration: const InputDecoration(
                    labelText: 'GitHub Review Action',
                    border: OutlineInputBorder(),
                  ),
                  items: const <DropdownMenuItem<String>>[
                    DropdownMenuItem<String>(
                      value: 'PENDING',
                      child: Text('📝 Create / Update Pending Review Draft'),
                    ),
                    DropdownMenuItem<String>(
                      value: 'COMMENT',
                      child: Text('💬 Submit Review Immediately (COMMENT)'),
                    ),
                    DropdownMenuItem<String>(
                      value: 'APPROVE',
                      child: Text('✅ Approve PR & Submit Comments (APPROVE)'),
                    ),
                    DropdownMenuItem<String>(
                      value: 'REQUEST_CHANGES',
                      child: Text('🔴 Request Changes (REQUEST_CHANGES)'),
                    ),
                  ],
                  onChanged: (String? v) {
                    if (v != null) {
                      setModalState(() => event = v);
                    }
                  },
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: summaryCtrl,
                  maxLines: 5,
                  decoration: const InputDecoration(
                    labelText: 'Optional Top-Level Review Summary',
                    border: OutlineInputBorder(),
                  ),
                ),
              ],
            ),
          ),
          actions: <Widget>[
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: const Text('Publish to GitHub'),
            ),
          ],
        ),
      ),
    );

    if (go == true) {
      try {
        await _api.publishGithubReview(
          sessionId: sess.sessionId,
          event: event,
          summaryBody: summaryCtrl.text.trim(),
        );
        _showSnack('✅ Published review ($event) to GitHub!');
        await _refreshState(silent: true);
      } catch (e) {
        _showSnack('Publish failed: $e', isError: true);
      }
    }
    summaryCtrl.dispose();
  }

  Future<void> _showGithubTokenDialog() async {
    final TextEditingController ctrl = TextEditingController();
    final String? tok = await showDialog<String>(
      context: context,
      builder: (BuildContext ctx) => AlertDialog(
        backgroundColor: const Color(0xFF161B22),
        title: const Text(
          '🔑 Configure GitHub Personal Access Token',
          style: TextStyle(fontSize: 15, color: Color(0xFFE6EDF3)),
        ),
        content: SizedBox(
          width: 480,
          child: TextField(
            controller: ctrl,
            obscureText: true,
            decoration: const InputDecoration(
              labelText: 'ghp_... or github_pat_...',
              border: OutlineInputBorder(),
            ),
          ),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(ctrl.text.trim()),
            child: const Text('Verify & Save'),
          ),
        ],
      ),
    );
    ctrl.dispose();
    if (tok != null && tok.isNotEmpty) {
      try {
        final String login = await _api.saveGithubToken(tok);
        _showSnack('✅ Connected GitHub account @$login');
        await _refreshState(silent: true);
      } catch (e) {
        _showSnack('GitHub token check failed: $e', isError: true);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final ReviewSession? sess = _state?.activeSession;
    final AgentLiveStatus? agent = _state?.agentStatus;

    final int stagedCount = sess?.activeThreads
            .where((ReviewThread t) => t.isStaged && !t.isGithubExisting)
            .length ??
        0;
    final int fixQueuedCount = sess?.activeThreads
            .where((ReviewThread t) => t.selectedForFix && !t.isFixed)
            .length ??
        0;

    return CallbackShortcuts(
      bindings: <ShortcutActivator, VoidCallback>{
        const SingleActivator(LogicalKeyboardKey.keyR, meta: true): () =>
            _refreshState(),
      },
      child: Focus(
        autofocus: true,
        child: Scaffold(
          body: Column(
            children: <Widget>[
              // Top App Header
              _buildTopHeader(sess, stagedCount, fixQueuedCount),

              // Live Agent Activity Banner (Antigravity CLI progress)
              if (agent != null && (agent.active || agent.completedJustNow))
                _buildAgentActivityBanner(agent),

              // Connection error banner (when server.py isn't started yet)
              if (_connectionError != null) _buildConnectionBanner(),

              // Main 3-Pane Body
              Expanded(
                child: _loading && _state == null
                    ? const Center(child: CircularProgressIndicator())
                    : sess == null
                        ? _buildEmptyWelcome()
                        : Row(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: <Widget>[
                              _buildLeftSidebar(sess),
                              Expanded(child: _buildMainPane(sess)),
                            ],
                          ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildTopHeader(
    ReviewSession? sess,
    int stagedCount,
    int fixQueuedCount,
  ) {
    final List<SessionSummary> sessions =
        _state?.sessions ?? const <SessionSummary>[];
    final GithubAuthInfo? auth = _state?.githubAuth;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
      decoration: const BoxDecoration(
        color: Color(0xFF161B22),
        border: Border(bottom: BorderSide(color: Color(0xFF21262D))),
      ),
      child: Wrap(
        spacing: 10,
        runSpacing: 8,
        alignment: WrapAlignment.spaceBetween,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: <Widget>[
          Row(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              const Icon(Icons.auto_awesome, color: Color(0xFF58A6FF), size: 20),
              const SizedBox(width: 8),
              const Text(
                'Quasar',
                style: TextStyle(
                  fontSize: 15.5,
                  fontWeight: FontWeight.w800,
                  color: Color(0xFFE6EDF3),
                ),
              ),
              const SizedBox(width: 12),
              if (sessions.isNotEmpty)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10),
                  decoration: BoxDecoration(
                    color: const Color(0xFF0D1117),
                    borderRadius: BorderRadius.circular(7),
                    border: Border.all(color: const Color(0xFF30363D)),
                  ),
                  child: DropdownButtonHideUnderline(
                    child: DropdownButton<String>(
                      value: sessions.any(
                        (SessionSummary s) =>
                            s.sessionId == (_selectedSessionId ?? sess?.sessionId),
                      )
                          ? (_selectedSessionId ?? sess?.sessionId)
                          : sessions.first.sessionId,
                      dropdownColor: const Color(0xFF161B22),
                      isDense: true,
                      style: const TextStyle(
                        fontSize: 12.5,
                        color: Color(0xFFE6EDF3),
                      ),
                      items: <DropdownMenuItem<String>>[
                        for (final SessionSummary s in sessions)
                          DropdownMenuItem<String>(
                            value: s.sessionId,
                            child: ConstrainedBox(
                              constraints: const BoxConstraints(maxWidth: 340),
                              child: Text(
                                s.title,
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                          ),
                      ],
                      onChanged: (String? nextId) {
                        if (nextId != null) {
                          setState(() => _selectedSessionId = nextId);
                          _refreshState();
                        }
                      },
                    ),
                  ),
                ),
            ],
          ),

          // Center Tab Switcher
          if (sess != null)
            SegmentedButton<_MainTab>(
              segments: <ButtonSegment<_MainTab>>[
                const ButtonSegment<_MainTab>(
                  value: _MainTab.studyGuide,
                  label: Text('📖 Study Guide', style: TextStyle(fontSize: 12)),
                ),
                ButtonSegment<_MainTab>(
                  value: _MainTab.diff,
                  label: Text(
                    '🔀 Diff (${_state?.filesDiff.length ?? 0})',
                    style: const TextStyle(fontSize: 12),
                  ),
                ),
                ButtonSegment<_MainTab>(
                  value: _MainTab.comments,
                  label: Text(
                    '💬 Comments (${sess.activeThreads.length})',
                    style: const TextStyle(fontSize: 12),
                  ),
                ),
              ],
              selected: <_MainTab>{_activeTab},
              style: const ButtonStyle(visualDensity: VisualDensity.compact),
              onSelectionChanged: (Set<_MainTab> s) =>
                  setState(() => _activeTab = s.first),
            ),

          // Right Action Buttons
          Wrap(
            spacing: 8,
            runSpacing: 6,
            children: <Widget>[
              FilledButton.icon(
                onPressed: _showStartReviewDialog,
                icon: const Icon(Icons.add, size: 16),
                label: const Text('New Review', style: TextStyle(fontSize: 12)),
              ),
              OutlinedButton(
                onPressed: () async {
                  try {
                    final String sid = await _api.startTutorial();
                    setState(() => _selectedSessionId = sid);
                    await _refreshState();
                  } catch (e) {
                    _showSnack('Tutorial failed: $e', isError: true);
                  }
                },
                child: const Text('🎓 Tutorial', style: TextStyle(fontSize: 12)),
              ),
              if (sess != null && fixQueuedCount > 0)
                FilledButton.tonalIcon(
                  onPressed: () async {
                    try {
                      final int n =
                          await _api.fixSelected(sessionId: sess.sessionId);
                      _showSnack(
                        '🔧 Dispatched $n comment fix(es) to Antigravity CLI!',
                      );
                      await _refreshState(silent: true);
                    } catch (e) {
                      _showSnack('$e', isError: true);
                    }
                  },
                  icon: const Icon(Icons.build, size: 14),
                  label: Text(
                    'Fix Queued ($fixQueuedCount)',
                    style: const TextStyle(fontSize: 12),
                  ),
                ),
              if (sess != null)
                OutlinedButton.icon(
                  onPressed: () => _showPublishDialog(sess),
                  icon: const Icon(Icons.cloud_upload_outlined, size: 15),
                  label: Text(
                    'Publish ($stagedCount)',
                    style: const TextStyle(fontSize: 12),
                  ),
                ),
              OutlinedButton(
                onPressed: _showGithubTokenDialog,
                child: Text(
                  auth?.configured == true ? '🔑 @${auth!.login}' : '🔑 GitHub Key',
                  style: const TextStyle(fontSize: 12),
                ),
              ),
              IconButton(
                tooltip: 'Refresh State',
                onPressed: _refreshState,
                icon: const Icon(Icons.refresh, size: 18),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildAgentActivityBanner(AgentLiveStatus agent) {
    final Color color = agent.completedJustNow
        ? const Color(0xFF3FB950)
        : const Color(0xFF58A6FF);
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        border: Border(
          bottom: BorderSide(color: color.withValues(alpha: 0.4)),
        ),
      ),
      child: Row(
        children: <Widget>[
          if (agent.active)
            const SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          else
            const Icon(Icons.check_circle, size: 16, color: Color(0xFF3FB950)),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              '${agent.stage}  •  ${agent.detail}${agent.subagents.isNotEmpty ? '  [${agent.subagents.join(', ')}]' : ''}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 12, color: Color(0xFFE6EDF3)),
            ),
          ),
          Text(
            '${agent.elapsedSeconds}s',
            style: const TextStyle(
              fontFamily: 'monospace',
              fontSize: 11.5,
              color: Color(0xFF8B949E),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildConnectionBanner() {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      color: const Color(0xFF3B1D1D),
      child: Row(
        children: <Widget>[
          const Icon(Icons.warning_amber, size: 16, color: Color(0xFFF85149)),
          const SizedBox(width: 8),
          Expanded(
            child: SelectableText(
              'Cannot reach local Quasar server at ${_api.baseUrl}. Run `./bin/quasar` in your terminal to start the local Antigravity CLI bridge.',
              style: const TextStyle(fontSize: 12, color: Color(0xFFFFB4AB)),
            ),
          ),
          TextButton(
            onPressed: _refreshState,
            child: const Text('Retry'),
          ),
        ],
      ),
    );
  }

  Widget _buildEmptyWelcome() {
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 520),
        child: Container(
          padding: const EdgeInsets.all(28),
          decoration: BoxDecoration(
            color: const Color(0xFF161B22),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: const Color(0xFF30363D)),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              const Icon(
                Icons.auto_awesome,
                size: 42,
                color: Color(0xFF58A6FF),
              ),
              const SizedBox(height: 12),
              const Text(
                'Quasar Standalone (Web & Desktop)',
                style: TextStyle(
                  fontSize: 19,
                  fontWeight: FontWeight.w800,
                  color: Color(0xFFE6EDF3),
                ),
              ),
              const SizedBox(height: 8),
              const Text(
                'Review any GitHub PR or local Git branch powered directly by the Antigravity CLI (`agy`) — with interactive CliffsNotes, flowcharts, and zero-jump inline comment Q&A.',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 13, color: Color(0xFF8B949E)),
              ),
              const SizedBox(height: 20),
              Wrap(
                spacing: 10,
                runSpacing: 10,
                alignment: WrapAlignment.center,
                children: <Widget>[
                  FilledButton.icon(
                    onPressed: _showStartReviewDialog,
                    icon: const Icon(Icons.rocket_launch, size: 16),
                    label: const Text('Review a PR or Branch'),
                  ),
                  OutlinedButton.icon(
                    onPressed: () async {
                      final String sid = await _api.startTutorial();
                      setState(() => _selectedSessionId = sid);
                      await _refreshState();
                    },
                    icon: const Icon(Icons.school_outlined, size: 16),
                    label: const Text('Try Sandbox Tutorial'),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildLeftSidebar(ReviewSession sess) {
    final List<ReviewThread> active = sess.activeThreads;
    final List<ReviewThread> newFindings = active
        .where((ReviewThread t) => !t.isGithubExisting && !t.isFixed)
        .toList();
    final List<ReviewThread> ghExisting = active
        .where((ReviewThread t) => t.isGithubExisting && !t.isFixed)
        .toList();

    return Container(
      width: 290,
      decoration: const BoxDecoration(
        border: Border(right: BorderSide(color: Color(0xFF21262D))),
      ),
      child: Material(
        color: const Color(0xFF0D1117),
        child: ListView(
          padding: const EdgeInsets.symmetric(vertical: 8),
          children: <Widget>[
          if (sess.chapters.isNotEmpty) ...<Widget>[
            const Padding(
              padding: EdgeInsets.fromLTRB(12, 6, 12, 4),
              child: Text(
                'READING TOUR',
                style: TextStyle(
                  fontSize: 10.5,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.6,
                  color: Color(0xFF8B949E),
                ),
              ),
            ),
            for (final ReviewChapter ch in sess.chapters)
              ExpansionTile(
                initiallyExpanded: true,
                dense: true,
                tilePadding: const EdgeInsets.symmetric(horizontal: 12),
                title: Text(
                  ch.title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    color: Color(0xFFE6EDF3),
                  ),
                ),
                children: <Widget>[
                  for (final TourStop stop in ch.stops)
                    ListTile(
                      dense: true,
                      visualDensity: VisualDensity.compact,
                      leading: Icon(
                        stop.read
                            ? Icons.check_circle
                            : Icons.radio_button_unchecked,
                        size: 15,
                        color: stop.read
                            ? const Color(0xFF3FB950)
                            : const Color(0xFF6E7681),
                      ),
                      title: Text(
                        '${stop.stopNumber} ${stop.title.isNotEmpty ? stop.title : stop.path}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 11.5,
                          color: Color(0xFFC9D1D9),
                        ),
                      ),
                      onTap: () => _handleJumpToStop(stop),
                    ),
                ],
              ),
            const Divider(height: 16),
          ],

          // Quasar Findings & Staged Comments
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 4, 12, 4),
            child: Text(
              'QUASAR FINDINGS (${newFindings.length})',
              style: const TextStyle(
                fontSize: 10.5,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.6,
                color: Color(0xFF8B949E),
              ),
            ),
          ),
          for (final ReviewThread t in newFindings)
            _SidebarThreadTile(
              thread: t,
              onTap: () => _handleJumpToThread(t),
            ),

          if (ghExisting.isNotEmpty) ...<Widget>[
            const Divider(height: 16),
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 4, 12, 4),
              child: Text(
                'GITHUB PR COMMENTS (${ghExisting.length})',
                style: const TextStyle(
                  fontSize: 10.5,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.6,
                  color: Color(0xFF8B949E),
                ),
              ),
            ),
            for (final ReviewThread t in ghExisting)
              _SidebarThreadTile(
                thread: t,
                onTap: () => _handleJumpToThread(t),
              ),
          ],
          ],
        ),
      ),
    );
  }

  Widget _buildMainPane(ReviewSession sess) {
    switch (_activeTab) {
      case _MainTab.studyGuide:
        return StudyGuideView(
          session: sess,
          onJumpToStop: _handleJumpToStop,
          onToggleStopRead: (TourStop stop) async {
            await _api.toggleStopRead(
              sessionId: sess.sessionId,
              stopId: stop.id,
            );
            await _refreshState(silent: true);
          },
        );
      case _MainTab.diff:
        return DiffViewer(
          files: _state?.filesDiff ?? const <FileDiff>[],
          threads: sess.activeThreads,
          isOwnedReview: sess.pr.isOwnedByUser,
          scrollRequest: _diffScrollReq,
          onThreadAction: (
            ReviewThread t,
            String act, [
            Map<String, dynamic> extra = const <String, dynamic>{},
          ]) async {
            final String msg = await _api.threadAction(
              sessionId: sess.sessionId,
              threadId: t.id,
              action: act,
              extra: extra,
            );
            _showSnack(msg);
            await _refreshState(silent: true);
          },
          onReplyInThread: (
            ReviewThread t,
            String q, {
            bool changeMode = false,
          }) async {
            await _api.replyInThread(
              sessionId: sess.sessionId,
              threadId: t.id,
              question: q,
              changeMode: changeMode,
            );
            _showSnack('💬 Sent to Antigravity CLI — answer will appear in place.');
            await _refreshState(silent: true);
          },
          onAskAtLine: (String path, int line, String q) async {
            await _api.askAtLine(
              sessionId: sess.sessionId,
              path: path,
              line: line,
              question: q,
            );
            _showSnack('💬 Asked Antigravity CLI on $path:L$line');
            await _refreshState(silent: true);
          },
          onOpenInExternalEditor: (String path, int line) async {
            try {
              await _api.openInEditor(
                sessionId: sess.sessionId,
                path: path,
                line: line,
              );
            } catch (e) {
              _showSnack('Could not open external editor: $e', isError: true);
            }
          },
        );
      case _MainTab.comments:
        final List<ReviewThread> threads = sess.activeThreads;
        if (threads.isEmpty) {
          return const Center(
            child: Text(
              'No review comments in this session.',
              style: TextStyle(color: Color(0xFF8B949E)),
            ),
          );
        }
        return ListView.separated(
          padding: const EdgeInsets.all(20),
          itemCount: threads.length,
          separatorBuilder: (_, _) => const SizedBox(height: 12),
          itemBuilder: (BuildContext ctx, int idx) {
            final ReviewThread t = threads[idx];
            return ThreadCard(
              thread: t,
              isOwnedReview: sess.pr.isOwnedByUser,
              onThreadAction: (
                String act, [
                Map<String, dynamic> extra = const <String, dynamic>{},
              ]) async {
                final String msg = await _api.threadAction(
                  sessionId: sess.sessionId,
                  threadId: t.id,
                  action: act,
                  extra: extra,
                );
                _showSnack(msg);
                await _refreshState(silent: true);
              },
              onReplyInThread: (String q, {bool changeMode = false}) async {
                await _api.replyInThread(
                  sessionId: sess.sessionId,
                  threadId: t.id,
                  question: q,
                  changeMode: changeMode,
                );
                _showSnack('💬 Sent to Antigravity CLI.');
                await _refreshState(silent: true);
              },
              onOpenFileAtLine: () => _handleJumpToThread(t),
              onOpenInExternalEditor: t.displayPath.isEmpty
                  ? null
                  : () => _api.openInEditor(
                        sessionId: sess.sessionId,
                        path: t.displayPath,
                        line: t.startLine,
                      ),
            );
          },
        );
    }
  }
}

class _SidebarThreadTile extends StatelessWidget {
  const _SidebarThreadTile({required this.thread, required this.onTap});

  final ReviewThread thread;
  final VoidCallback onTap;

  String _iconPrefix() {
    if (thread.isFixed) {
      return '✅';
    }
    if (thread.isUncertain) {
      return '🤔';
    }
    if (thread.isGithubExisting) {
      if (thread.triageStatus == 'addressed_or_refuted') {
        return '✅';
      }
      if (thread.triageStatus == 'partially_addressed') {
        return '🟡';
      }
      if (thread.triageStatus == 'recommended_fix') {
        return '🛠️';
      }
      return '❓';
    }
    final String sev = thread.severity.toUpperCase();
    if (sev.contains('BLOCK')) {
      return '🔴';
    }
    if (sev.contains('DESIGN')) {
      return '🟡';
    }
    return '🟢';
  }

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(_iconPrefix(), style: const TextStyle(fontSize: 12)),
            const SizedBox(width: 7),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    '${thread.selectedForFix ? '🔧 ' : ''}${thread.cleanTitle}',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 12,
                      color: Color(0xFFE6EDF3),
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    '${thread.shortFileName}:L${thread.startLine}',
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 10.5,
                      color: Color(0xFF6E7681),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
