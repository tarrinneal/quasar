import 'package:flutter/material.dart';
import '../models/quasar_models.dart';
import 'thread_card.dart';

class DiffScrollRequest {
  const DiffScrollRequest({
    required this.nonce,
    required this.filePath,
    required this.line,
    this.threadId,
  });

  final int nonce;
  final String filePath;
  final int line;
  final String? threadId;
}

class DiffViewer extends StatefulWidget {
  const DiffViewer({
    super.key,
    required this.files,
    required this.threads,
    required this.isOwnedReview,
    required this.onThreadAction,
    required this.onReplyInThread,
    required this.onAskAtLine,
    required this.onOpenInExternalEditor,
    this.tourStops = const <TourStop>[],
    this.activeStopIndex = 0,
    this.onStepTourStop,
    this.onOpenStudyGuide,
    this.scrollRequest,
  });

  final List<FileDiff> files;
  final List<ReviewThread> threads;
  final List<TourStop> tourStops;
  final int activeStopIndex;
  final void Function(int delta)? onStepTourStop;
  final VoidCallback? onOpenStudyGuide;
  final bool isOwnedReview;
  final Future<void> Function(
    ReviewThread thread,
    String action, [
    Map<String, dynamic> extra,
  ]) onThreadAction;
  final Future<void> Function(
    ReviewThread thread,
    String question, {
    bool changeMode,
  }) onReplyInThread;
  final Future<void> Function(
    String filePath,
    int line,
    String question, {
    String mode,
    String severity,
  }) onAskAtLine;
  final Future<void> Function(String filePath, int line)
      onOpenInExternalEditor;
  final DiffScrollRequest? scrollRequest;

  @override
  State<DiffViewer> createState() => _DiffViewerState();
}

class _DiffViewerState extends State<DiffViewer> {
  String _selectedFilePath = '';
  bool _splitMode = true;
  bool _foldUnchanged = true;
  final Set<int> _expandedFoldBlocks = <int>{};
  int? _askAtLineOpen;
  String? _highlightedThreadId;
  int? _highlightedLine;
  final TextEditingController _lineQuestionCtrl = TextEditingController();
  String _lineCommentSeverity = 'DESIGN';
  bool _submittingLineQ = false;

  final ScrollController _diffScrollController = ScrollController();
  final Map<int, GlobalKey> _lineKeys = <int, GlobalKey>{};
  final Map<String, GlobalKey> _threadKeys = <String, GlobalKey>{};
  int _lastHandledNonce = -1;

  @override
  void initState() {
    super.initState();
    _syncSelectedFile();
    _maybeApplyScrollRequest();
  }

  @override
  void didUpdateWidget(covariant DiffViewer oldWidget) {
    super.didUpdateWidget(oldWidget);
    _syncSelectedFile();
    if (widget.scrollRequest != null &&
        widget.scrollRequest!.nonce != _lastHandledNonce) {
      _maybeApplyScrollRequest();
    }
  }

  @override
  void dispose() {
    _diffScrollController.dispose();
    _lineQuestionCtrl.dispose();
    super.dispose();
  }

  void _syncSelectedFile() {
    if (widget.files.isEmpty) {
      _selectedFilePath = '';
      return;
    }
    if (widget.scrollRequest != null &&
        widget.scrollRequest!.nonce != _lastHandledNonce) {
      final String targetPath = widget.scrollRequest!.filePath;
      if (widget.files.any((FileDiff f) => f.path == targetPath)) {
        _selectedFilePath = targetPath;
        return;
      }
    }
    if (!widget.files.any((FileDiff f) => f.path == _selectedFilePath)) {
      _selectedFilePath = widget.files.first.path;
    }
  }

  void _maybeApplyScrollRequest() {
    final DiffScrollRequest? req = widget.scrollRequest;
    if (req == null || req.nonce == _lastHandledNonce) {
      return;
    }
    _lastHandledNonce = req.nonce;
    if (widget.files.any((FileDiff f) => f.path == req.filePath)) {
      _selectedFilePath = req.filePath;
    }
    _highlightedThreadId = req.threadId;
    _highlightedLine = req.line;

    WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToTarget(req));
  }

  void _scrollToTarget(DiffScrollRequest req) {
    if (!mounted) {
      return;
    }
    final GlobalKey? tKey =
        req.threadId != null ? _threadKeys[req.threadId!] : null;
    final BuildContext? targetCtx =
        tKey?.currentContext ?? _lineKeys[req.line]?.currentContext;

    if (targetCtx != null) {
      Scrollable.ensureVisible(
        targetCtx,
        alignment: 0.28,
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOutCubic,
      );
      return;
    }

    // If the item is off-screen in a long file, jump via proportional offset first,
    // then refine with `Scrollable.ensureVisible` once built.
    final FileDiff? activeFile = _currentFile;
    if (activeFile != null &&
        activeFile.rows.isNotEmpty &&
        _diffScrollController.hasClients) {
      final int idx = activeFile.rows.indexWhere(
        (DiffRow r) => (r.headLine ?? r.baseLine ?? 1) >= req.line,
      );
      if (idx >= 0) {
        final double frac = idx / activeFile.rows.length;
        final double maxExtent =
            _diffScrollController.position.maxScrollExtent;
        _diffScrollController.jumpTo((maxExtent * frac).clamp(0.0, maxExtent));
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (!mounted) {
            return;
          }
          final BuildContext? ctx2 =
              (req.threadId != null
                      ? _threadKeys[req.threadId!]?.currentContext
                      : null) ??
                  _lineKeys[req.line]?.currentContext;
          if (ctx2 != null) {
            Scrollable.ensureVisible(
              ctx2,
              alignment: 0.28,
              duration: const Duration(milliseconds: 160),
              curve: Curves.easeOut,
            );
          }
        });
      }
    }
  }

  FileDiff? get _currentFile {
    if (widget.files.isEmpty) {
      return null;
    }
    return widget.files.firstWhere(
      (FileDiff f) => f.path == _selectedFilePath,
      orElse: () => widget.files.first,
    );
  }

  Future<void> _submitLineQuestion(
    String filePath,
    int line, {
    String mode = 'manual',
  }) async {
    final String q = _lineQuestionCtrl.text.trim();
    if ((q.isEmpty && mode != 'ai_comment') || _submittingLineQ) {
      return;
    }
    setState(() => _submittingLineQ = true);
    try {
      await widget.onAskAtLine(
        filePath,
        line,
        q,
        mode: mode,
        severity: _lineCommentSeverity,
      );
      _lineQuestionCtrl.clear();
      if (mounted) {
        setState(() => _askAtLineOpen = null);
      }
    } finally {
      if (mounted) {
        setState(() => _submittingLineQ = false);
      }
    }
  }

  List<_DisplaySlice> _buildSlices(
    FileDiff file,
    Map<int, List<ReviewThread>> threadsByLine,
  ) {
    final List<DiffRow> rows = file.rows;
    if (!_foldUnchanged || rows.length < 30) {
      return <_DisplaySlice>[
        for (int i = 0; i < rows.length; i++) _DisplaySlice.single(rows[i], i),
      ];
    }

    // Mark lines near changes, threads, or highlighted target line as always-visible
    final Set<int> keepIndices = <int>{};
    for (int i = 0; i < rows.length; i++) {
      final DiffRow r = rows[i];
      final int ln = r.headLine ?? r.baseLine ?? 1;
      final bool isInteresting = r.type != 'context' ||
          threadsByLine.containsKey(ln) ||
          ln == _highlightedLine ||
          ln == _askAtLineOpen;
      if (isInteresting) {
        for (int k = i - 4; k <= i + 4; k++) {
          if (k >= 0 && k < rows.length) {
            keepIndices.add(k);
          }
        }
      }
    }

    final List<_DisplaySlice> out = <_DisplaySlice>[];
    int i = 0;
    while (i < rows.length) {
      if (keepIndices.contains(i) || _expandedFoldBlocks.contains(i)) {
        if (_expandedFoldBlocks.contains(i)) {
          int end = i;
          while (end < rows.length && !keepIndices.contains(end)) {
            out.add(_DisplaySlice.single(rows[end], end));
            end++;
          }
          i = end;
        } else {
          out.add(_DisplaySlice.single(rows[i], i));
          i++;
        }
      } else {
        final int foldStart = i;
        while (i < rows.length && !keepIndices.contains(i)) {
          i++;
        }
        final int count = i - foldStart;
        if (count <= 3) {
          for (int k = foldStart; k < i; k++) {
            out.add(_DisplaySlice.single(rows[k], k));
          }
        } else {
          out.add(_DisplaySlice.folded(foldStart, count));
        }
      }
    }
    return out;
  }

  @override
  Widget build(BuildContext context) {
    if (widget.files.isEmpty) {
      return const Center(
        child: Text(
          'No file diffs loaded for this session yet.',
          style: TextStyle(color: Color(0xFF8B949E)),
        ),
      );
    }

    final FileDiff activeFile = _currentFile!;
    final List<ReviewThread> fileThreads = widget.threads
        .where(
          (ReviewThread t) =>
              t.status != 'dismissed' && t.path == activeFile.path,
        )
        .toList();
    final Map<int, List<ReviewThread>> threadsByLine =
        <int, List<ReviewThread>>{};
    for (final ReviewThread t in fileThreads) {
      final int anchor = t.endLine > 0 ? t.endLine : t.startLine;
      threadsByLine.putIfAbsent(anchor, () => <ReviewThread>[]).add(t);
    }

    final List<_DisplaySlice> slices = _buildSlices(activeFile, threadsByLine);

    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        // File List Pane
        Container(
          width: 245,
          decoration: const BoxDecoration(
            color: Color(0xFF0D1117),
            border: Border(
              right: BorderSide(color: Color(0xFF21262D)),
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: Text(
                  'CHANGED FILES (${widget.files.length})',
                  style: const TextStyle(
                    fontSize: 10.5,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.6,
                    color: Color(0xFF8B949E),
                  ),
                ),
              ),
              Expanded(
                child: ListView.builder(
                  itemCount: widget.files.length,
                  itemBuilder: (BuildContext ctx, int idx) {
                    final FileDiff f = widget.files[idx];
                    final bool selected = f.path == activeFile.path;
                    final int tCount = widget.threads
                        .where(
                          (ReviewThread t) =>
                              t.status != 'dismissed' && t.path == f.path,
                        )
                        .length;
                    return InkWell(
                      onTap: () => setState(() {
                        _selectedFilePath = f.path;
                        _expandedFoldBlocks.clear();
                        _askAtLineOpen = null;
                      }),
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 10,
                          vertical: 7,
                        ),
                        decoration: BoxDecoration(
                          color: selected
                              ? const Color(0xFF1F2937)
                              : Colors.transparent,
                          border: Border(
                            left: BorderSide(
                              color: selected
                                  ? const Color(0xFF58A6FF)
                                  : Colors.transparent,
                              width: 2.5,
                            ),
                          ),
                        ),
                        child: Row(
                          children: <Widget>[
                            Expanded(
                              child: Tooltip(
                                message: f.path,
                                child: Text(
                                  f.shortName,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: TextStyle(
                                    fontSize: 12,
                                    fontWeight: selected
                                        ? FontWeight.w600
                                        : FontWeight.w400,
                                    color: selected
                                        ? const Color(0xFFE6EDF3)
                                        : const Color(0xFFC9D1D9),
                                  ),
                                ),
                              ),
                            ),
                            if (tCount > 0)
                              Container(
                                margin: const EdgeInsets.only(right: 5),
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 5,
                                  vertical: 1,
                                ),
                                decoration: BoxDecoration(
                                  color: const Color(0xFF388BFD)
                                      .withValues(alpha: 0.22),
                                  borderRadius: BorderRadius.circular(10),
                                ),
                                child: Text(
                                  '💬$tCount',
                                  style: const TextStyle(
                                    fontSize: 10,
                                    color: Color(0xFF79C0FF),
                                  ),
                                ),
                              ),
                            Text(
                              '+${f.additions}',
                              style: const TextStyle(
                                fontFamily: 'monospace',
                                fontSize: 10.5,
                                color: Color(0xFF3FB950),
                              ),
                            ),
                            const SizedBox(width: 4),
                            Text(
                              '-${f.deletions}',
                              style: const TextStyle(
                                fontFamily: 'monospace',
                                fontSize: 10.5,
                                color: Color(0xFFF85149),
                              ),
                            ),
                          ],
                        ),
                      ),
                    );
                  },
                ),
              ),
            ],
          ),
        ),

        // Diff Rows + Inline Comment Threads
        Expanded(
          child: Column(
            children: <Widget>[
              if (widget.tourStops.isNotEmpty)
                Builder(
                  builder: (BuildContext ctx) {
                    final int safeIdx = widget.activeStopIndex
                        .clamp(0, widget.tourStops.length - 1);
                    final TourStop curStop = widget.tourStops[safeIdx];
                    final String summaryText = curStop.cliffsNotes.isNotEmpty
                        ? curStop.cliffsNotes
                        : curStop.title;
                    return Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 14,
                        vertical: 6,
                      ),
                      decoration: const BoxDecoration(
                        color: Color(0xFF0E1D28),
                        border: Border(
                          bottom: BorderSide(color: Color(0xFF21262D)),
                        ),
                      ),
                      child: Row(
                        children: <Widget>[
                          Text(
                            '📘 Stop ${safeIdx + 1}/${widget.tourStops.length} (${curStop.stopNumber})',
                            style: const TextStyle(
                              fontSize: 12,
                              fontWeight: FontWeight.w700,
                              color: Color(0xFF79C0FF),
                            ),
                          ),
                          const SizedBox(width: 10),
                          Expanded(
                            child: Text(
                              summaryText,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                fontSize: 12,
                                color: Color(0xFFC9D1D9),
                              ),
                            ),
                          ),
                          const SizedBox(width: 8),
                          OutlinedButton.icon(
                            onPressed: widget.onStepTourStop == null
                                ? null
                                : () => widget.onStepTourStop!(-1),
                            icon: const Icon(Icons.arrow_back, size: 14),
                            label: const Text(
                              'Prev',
                              style: TextStyle(fontSize: 11.5),
                            ),
                            style: OutlinedButton.styleFrom(
                              visualDensity: VisualDensity.compact,
                            ),
                          ),
                          const SizedBox(width: 6),
                          FilledButton.tonalIcon(
                            onPressed: widget.onStepTourStop == null
                                ? null
                                : () => widget.onStepTourStop!(1),
                            icon: const Icon(Icons.arrow_forward, size: 14),
                            label: const Text(
                              'Next Stop',
                              style: TextStyle(fontSize: 11.5),
                            ),
                            style: FilledButton.styleFrom(
                              visualDensity: VisualDensity.compact,
                            ),
                          ),
                          if (widget.onOpenStudyGuide != null) ...<Widget>[
                            const SizedBox(width: 6),
                            TextButton.icon(
                              onPressed: widget.onOpenStudyGuide,
                              icon: const Icon(Icons.menu_book, size: 14),
                              label: const Text(
                                'Study Guide',
                                style: TextStyle(fontSize: 11.5),
                              ),
                              style: TextButton.styleFrom(
                                visualDensity: VisualDensity.compact,
                              ),
                            ),
                          ],
                        ],
                      ),
                    );
                  },
                ),

              // File toolbar
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                decoration: const BoxDecoration(
                  color: Color(0xFF161B22),
                  border: Border(
                    bottom: BorderSide(color: Color(0xFF21262D)),
                  ),
                ),
                child: Row(
                  children: <Widget>[
                    Expanded(
                      child: SelectableText(
                        activeFile.path,
                        style: const TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 12.5,
                          fontWeight: FontWeight.w600,
                          color: Color(0xFFE6EDF3),
                        ),
                      ),
                    ),
                    FilterChip(
                      label: const Text(
                        'Fold Unchanged',
                        style: TextStyle(fontSize: 11.5),
                      ),
                      selected: _foldUnchanged,
                      visualDensity: VisualDensity.compact,
                      onSelected: (bool v) =>
                          setState(() => _foldUnchanged = v),
                    ),
                    const SizedBox(width: 8),
                    SegmentedButton<bool>(
                      segments: const <ButtonSegment<bool>>[
                        ButtonSegment<bool>(
                          value: true,
                          label: Text('Split', style: TextStyle(fontSize: 11.5)),
                        ),
                        ButtonSegment<bool>(
                          value: false,
                          label:
                              Text('Unified', style: TextStyle(fontSize: 11.5)),
                        ),
                      ],
                      selected: <bool>{_splitMode},
                      style: const ButtonStyle(
                        visualDensity: VisualDensity.compact,
                      ),
                      onSelectionChanged: (Set<bool> s) =>
                          setState(() => _splitMode = s.first),
                    ),
                    const SizedBox(width: 8),
                    OutlinedButton.icon(
                      onPressed: () => widget.onOpenInExternalEditor(
                        activeFile.path,
                        _highlightedLine ?? 1,
                      ),
                      icon: const Icon(Icons.open_in_new, size: 14),
                      label: const Text(
                        'Open in Editor',
                        style: TextStyle(fontSize: 11.5),
                      ),
                    ),
                  ],
                ),
              ),

              Expanded(
                child: ListView.builder(
                  controller: _diffScrollController,
                  itemCount: slices.length,
                  itemBuilder: (BuildContext ctx, int idx) {
                    final _DisplaySlice slice = slices[idx];
                    if (slice.isFold) {
                      return InkWell(
                        onTap: () => setState(
                          () => _expandedFoldBlocks.add(slice.foldStartIdx),
                        ),
                        child: Container(
                          width: double.infinity,
                          padding: const EdgeInsets.symmetric(
                            horizontal: 14,
                            vertical: 5,
                          ),
                          color: const Color(0xFF111822),
                          child: Text(
                            '⋯ ${slice.foldedCount} unchanged lines — click to expand',
                            style: const TextStyle(
                              fontFamily: 'monospace',
                              fontSize: 11,
                              color: Color(0xFF58A6FF),
                            ),
                          ),
                        ),
                      );
                    }

                    final DiffRow row = slice.row!;
                    final int lineKeyNum =
                        row.headLine ?? row.baseLine ?? (slice.rowIdx + 1);
                    final GlobalKey lineKey = _lineKeys.putIfAbsent(
                      lineKeyNum,
                      () => GlobalKey(),
                    );
                    final List<ReviewThread> inlineThreads = row.headLine != null
                        ? (threadsByLine[row.headLine!] ??
                            const <ReviewThread>[])
                        : const <ReviewThread>[];
                    final bool isTargetLine = _highlightedLine == lineKeyNum;

                    return Column(
                      key: lineKey,
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: <Widget>[
                        _DiffCodeRow(
                          row: row,
                          splitMode: _splitMode,
                          highlighted: isTargetLine,
                          onAskTap: () => setState(() {
                            _askAtLineOpen = _askAtLineOpen == lineKeyNum
                                ? null
                                : lineKeyNum;
                          }),
                        ),
                        if (_askAtLineOpen == lineKeyNum)
                          Container(
                            padding: const EdgeInsets.all(10),
                            color: const Color(0xFF131B26),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: <Widget>[
                                Row(
                                  children: <Widget>[
                                    Expanded(
                                      child: TextField(
                                        controller: _lineQuestionCtrl,
                                        autofocus: true,
                                        minLines: 1,
                                        maxLines: 3,
                                        onSubmitted: (_) => _submitLineQuestion(
                                          activeFile.path,
                                          lineKeyNum,
                                          mode: 'manual',
                                        ),
                                        style: const TextStyle(
                                          fontSize: 12.5,
                                          color: Color(0xFFE6EDF3),
                                        ),
                                        decoration: InputDecoration(
                                          isDense: true,
                                          filled: true,
                                          fillColor: const Color(0xFF0D1117),
                                          hintText:
                                              'Add your review comment on ${activeFile.shortName}:L$lineKeyNum, tell Quasar what to comment on, or ask a question...',
                                          border: OutlineInputBorder(
                                            borderRadius:
                                                BorderRadius.circular(6),
                                          ),
                                        ),
                                      ),
                                    ),
                                    const SizedBox(width: 4),
                                    IconButton(
                                      onPressed: () =>
                                          setState(() => _askAtLineOpen = null),
                                      icon: const Icon(Icons.close, size: 16),
                                    ),
                                  ],
                                ),
                                const SizedBox(height: 8),
                                Wrap(
                                  spacing: 8,
                                  runSpacing: 6,
                                  alignment: WrapAlignment.spaceBetween,
                                  crossAxisAlignment: WrapCrossAlignment.center,
                                  children: <Widget>[
                                    Row(
                                      mainAxisSize: MainAxisSize.min,
                                      children: <Widget>[
                                        const Text(
                                          'Severity:',
                                          style: TextStyle(
                                            fontSize: 11.5,
                                            color: Color(0xFF8B949E),
                                          ),
                                        ),
                                        const SizedBox(width: 6),
                                        DropdownButton<String>(
                                          value: _lineCommentSeverity,
                                          isDense: true,
                                          dropdownColor:
                                              const Color(0xFF21262D),
                                          style: const TextStyle(
                                            fontSize: 12,
                                            color: Color(0xFFE6EDF3),
                                          ),
                                          items: const <DropdownMenuItem<
                                              String>>[
                                            DropdownMenuItem<String>(
                                              value: 'DESIGN',
                                              child: Text('🟡 Design'),
                                            ),
                                            DropdownMenuItem<String>(
                                              value: 'NIT',
                                              child: Text('🟢 Nit'),
                                            ),
                                            DropdownMenuItem<String>(
                                              value: 'BLOCKING',
                                              child: Text('🔴 Blocking'),
                                            ),
                                          ],
                                          onChanged: (String? v) {
                                            if (v != null) {
                                              setState(() =>
                                                  _lineCommentSeverity = v);
                                            }
                                          },
                                        ),
                                      ],
                                    ),
                                    Wrap(
                                      spacing: 6,
                                      runSpacing: 6,
                                      children: <Widget>[
                                        OutlinedButton(
                                          onPressed: _submittingLineQ
                                              ? null
                                              : () => _submitLineQuestion(
                                                    activeFile.path,
                                                    lineKeyNum,
                                                    mode: 'qa',
                                                  ),
                                          child:
                                              const Text('💬 Ask Question'),
                                        ),
                                        OutlinedButton(
                                          onPressed: _submittingLineQ
                                              ? null
                                              : () => _submitLineQuestion(
                                                    activeFile.path,
                                                    lineKeyNum,
                                                    mode: 'ai_comment',
                                                  ),
                                          child: const Text(
                                            '✨ Ask Quasar to Comment',
                                          ),
                                        ),
                                        FilledButton(
                                          onPressed: _submittingLineQ
                                              ? null
                                              : () => _submitLineQuestion(
                                                    activeFile.path,
                                                    lineKeyNum,
                                                    mode: 'manual',
                                                  ),
                                          child:
                                              const Text('➕ Add Comment'),
                                        ),
                                      ],
                                    ),
                                  ],
                                ),
                              ],
                            ),
                          ),
                        for (final ReviewThread th in inlineThreads)
                          Container(
                            key: _threadKeys.putIfAbsent(
                              th.id,
                              () => GlobalKey(),
                            ),
                            padding: const EdgeInsets.symmetric(
                              horizontal: 18,
                              vertical: 8,
                            ),
                            decoration: const BoxDecoration(
                              color: Color(0xFF0B0F16),
                              border: Border(
                                top: BorderSide(color: Color(0xFF21262D)),
                                bottom: BorderSide(color: Color(0xFF21262D)),
                              ),
                            ),
                            child: ThreadCard(
                              thread: th,
                              isOwnedReview: widget.isOwnedReview,
                              highlighted: _highlightedThreadId == th.id,
                              onThreadAction: (
                                String act, [
                                Map<String, dynamic> extra =
                                    const <String, dynamic>{},
                              ]) =>
                                  widget.onThreadAction(th, act, extra),
                              onReplyInThread: (
                                String q, {
                                bool changeMode = false,
                              }) =>
                                  widget.onReplyInThread(
                                th,
                                q,
                                changeMode: changeMode,
                              ),
                              onOpenFileAtLine: () {},
                              onOpenInExternalEditor: () =>
                                  widget.onOpenInExternalEditor(
                                activeFile.path,
                                th.startLine,
                              ),
                            ),
                          ),
                      ],
                    );
                  },
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _DisplaySlice {
  const _DisplaySlice._({
    this.row,
    required this.rowIdx,
    required this.isFold,
    this.foldStartIdx = 0,
    this.foldedCount = 0,
  });

  factory _DisplaySlice.single(DiffRow row, int idx) =>
      _DisplaySlice._(row: row, rowIdx: idx, isFold: false);

  factory _DisplaySlice.folded(int startIdx, int count) => _DisplaySlice._(
        rowIdx: startIdx,
        isFold: true,
        foldStartIdx: startIdx,
        foldedCount: count,
      );

  final DiffRow? row;
  final int rowIdx;
  final bool isFold;
  final int foldStartIdx;
  final int foldedCount;
}

class _DiffCodeRow extends StatelessWidget {
  const _DiffCodeRow({
    required this.row,
    required this.splitMode,
    required this.highlighted,
    required this.onAskTap,
  });

  final DiffRow row;
  final bool splitMode;
  final bool highlighted;
  final VoidCallback onAskTap;

  @override
  Widget build(BuildContext context) {
    const Color addBg = Color(0x262EA043);
    const Color delBg = Color(0x26F85149);
    const Color modRightBg = Color(0x1F3FB950);
    const Color modLeftBg = Color(0x1FF85149);
    const Color targetHighlightBg = Color(0x33388BFD);

    if (!splitMode) {
      final Color bg = highlighted
          ? targetHighlightBg
          : row.type == 'add'
              ? addBg
              : row.type == 'del'
                  ? delBg
                  : row.type == 'mod'
                      ? modRightBg
                      : const Color(0xFF0D1117);
      final String prefix = row.type == 'del'
          ? '-'
          : row.type == 'add' || row.type == 'mod'
              ? '+'
              : ' ';
      final String code =
          row.type == 'del' ? row.baseText : (row.headText.isNotEmpty ? row.headText : row.baseText);
      return Container(
        color: bg,
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            _LineGutter(
              lineNum: row.baseLine,
              onTap: onAskTap,
            ),
            _LineGutter(
              lineNum: row.headLine,
              showPlus: true,
              onTap: onAskTap,
            ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              child: Text(
                prefix,
                style: TextStyle(
                  fontFamily: 'monospace',
                  fontSize: 12,
                  color: prefix == '+'
                      ? const Color(0xFF3FB950)
                      : prefix == '-'
                          ? const Color(0xFFF85149)
                          : const Color(0xFF8B949E),
                ),
              ),
            ),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 2, horizontal: 4),
                child: _SyntaxLine(code: code),
              ),
            ),
          ],
        ),
      );
    }

    final Color leftBg = highlighted
        ? targetHighlightBg
        : (row.type == 'del'
            ? delBg
            : row.type == 'mod'
                ? modLeftBg
                : const Color(0xFF0D1117));
    final Color rightBg = highlighted
        ? targetHighlightBg
        : (row.type == 'add'
            ? addBg
            : row.type == 'mod'
                ? modRightBg
                : const Color(0xFF0D1117));

    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Expanded(
            child: Container(
              decoration: BoxDecoration(
                color: leftBg,
                border: const Border(
                  right: BorderSide(color: Color(0xFF21262D)),
                ),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  _LineGutter(lineNum: row.baseLine, onTap: onAskTap),
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                        vertical: 2,
                        horizontal: 6,
                      ),
                      child: _SyntaxLine(code: row.baseText),
                    ),
                  ),
                ],
              ),
            ),
          ),
          Expanded(
            child: Container(
              color: rightBg,
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  _LineGutter(
                    lineNum: row.headLine,
                    showPlus: row.headLine != null,
                    onTap: onAskTap,
                  ),
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                        vertical: 2,
                        horizontal: 6,
                      ),
                      child: _SyntaxLine(code: row.headText),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _LineGutter extends StatelessWidget {
  const _LineGutter({
    required this.lineNum,
    required this.onTap,
    this.showPlus = false,
  });

  final int? lineNum;
  final VoidCallback onTap;
  final bool showPlus;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: lineNum == null ? null : onTap,
      child: SizedBox(
        width: showPlus ? 54 : 44,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
          child: Text(
            lineNum == null ? '' : '${lineNum!}${showPlus ? ' +' : ''}',
            textAlign: TextAlign.right,
            style: const TextStyle(
              fontFamily: 'monospace',
              fontSize: 11,
              color: Color(0xFF6E7681),
            ),
          ),
        ),
      ),
    );
  }
}

class _SyntaxLine extends StatelessWidget {
  const _SyntaxLine({required this.code});

  final String code;

  static final RegExp _tokRe = RegExp(
    r'(//.*$|#.*$|"(?:\\.|[^"\\])*"|'
    r"'(?:\\.|[^'\\])*'"
    r'|@[A-Za-z_]\w*|\b(?:class|struct|enum|extension|func|fun|def|void|int|double|bool|String|final|const|var|val|let|if|else|for|while|switch|case|return|async|await|import|package|private|public|internal|static|override|try|catch|throw|null|nil|true|false)\b|\b\d+\b)',
  );

  @override
  Widget build(BuildContext context) {
    if (code.isEmpty) {
      return const SizedBox(height: 16);
    }
    final List<TextSpan> spans = <TextSpan>[];
    int cursor = 0;
    for (final RegExpMatch m in _tokRe.allMatches(code)) {
      if (m.start > cursor) {
        spans.add(TextSpan(text: code.substring(cursor, m.start)));
      }
      final String tok = m.group(0)!;
      Color tokColor = const Color(0xFFE6EDF3);
      if (tok.startsWith('//') || tok.startsWith('#')) {
        tokColor = const Color(0xFF8B949E);
      } else if (tok.startsWith('"') || tok.startsWith("'")) {
        tokColor = const Color(0xFFA5D6FF);
      } else if (tok.startsWith('@')) {
        tokColor = const Color(0xFFD2A8FF);
      } else if (RegExp(r'^\d+$').hasMatch(tok)) {
        tokColor = const Color(0xFF79C0FF);
      } else {
        tokColor = const Color(0xFFFF7B72);
      }
      spans.add(TextSpan(text: tok, style: TextStyle(color: tokColor)));
      cursor = m.end;
    }
    if (cursor < code.length) {
      spans.add(TextSpan(text: code.substring(cursor)));
    }

    return SelectableText.rich(
      TextSpan(
        style: const TextStyle(
          fontFamily: 'monospace',
          fontSize: 12,
          height: 1.38,
          color: Color(0xFFE6EDF3),
        ),
        children: spans,
      ),
    );
  }
}
