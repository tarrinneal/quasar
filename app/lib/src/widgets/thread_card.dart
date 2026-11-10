import 'package:flutter/material.dart';
import '../models/quasar_models.dart';
import 'markdown_text.dart';

class ThreadCard extends StatefulWidget {
  const ThreadCard({
    super.key,
    required this.thread,
    required this.isOwnedReview,
    required this.onThreadAction,
    required this.onReplyInThread,
    required this.onOpenFileAtLine,
    this.onOpenInExternalEditor,
    this.compact = false,
    this.highlighted = false,
  });

  final ReviewThread thread;
  final bool isOwnedReview;
  final Future<void> Function(String action, [Map<String, dynamic> extra])
      onThreadAction;
  final Future<void> Function(String question, {bool changeMode})
      onReplyInThread;
  final VoidCallback onOpenFileAtLine;
  final VoidCallback? onOpenInExternalEditor;
  final bool compact;
  final bool highlighted;

  @override
  State<ThreadCard> createState() => _ThreadCardState();
}

class _ThreadCardState extends State<ThreadCard> {
  bool _replyOpen = false;
  bool _changeMode = false;
  bool _submittingReply = false;
  final TextEditingController _replyCtrl = TextEditingController();

  @override
  void dispose() {
    _replyCtrl.dispose();
    super.dispose();
  }

  Future<void> _submitReply() async {
    final String text = _replyCtrl.text.trim();
    if (text.isEmpty || _submittingReply) {
      return;
    }
    setState(() => _submittingReply = true);
    try {
      await widget.onReplyInThread(text, changeMode: _changeMode);
      _replyCtrl.clear();
      if (mounted) {
        setState(() => _replyOpen = false);
      }
    } finally {
      if (mounted) {
        setState(() => _submittingReply = false);
      }
    }
  }

  Future<void> _showEditDraftDialog() async {
    final TextEditingController ctrl =
        TextEditingController(text: widget.thread.draftComment);
    final String? updated = await showDialog<String>(
      context: context,
      builder: (BuildContext ctx) => AlertDialog(
        backgroundColor: const Color(0xFF161B22),
        title: const Text(
          'Edit Draft Review Comment',
          style: TextStyle(fontSize: 15, color: Color(0xFFE6EDF3)),
        ),
        content: SizedBox(
          width: 560,
          child: TextField(
            controller: ctrl,
            maxLines: 7,
            style: const TextStyle(fontSize: 13, color: Color(0xFFE6EDF3)),
            decoration: const InputDecoration(
              border: OutlineInputBorder(),
              hintText: 'GitHub comment markdown...',
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
            child: const Text('Save Draft'),
          ),
        ],
      ),
    );
    ctrl.dispose();
    if (updated != null && updated.isNotEmpty) {
      await widget.onThreadAction(
        'editDraft',
        <String, dynamic>{'draftComment': updated},
      );
    }
  }

  Future<void> _showCustomSolutionDialog() async {
    final TextEditingController ctrl =
        TextEditingController(text: widget.thread.selectedSolution);
    final String? custom = await showDialog<String>(
      context: context,
      builder: (BuildContext ctx) => AlertDialog(
        backgroundColor: const Color(0xFF161B22),
        title: const Text(
          'Custom Solution Approach',
          style: TextStyle(fontSize: 15, color: Color(0xFFE6EDF3)),
        ),
        content: SizedBox(
          width: 520,
          child: TextField(
            controller: ctrl,
            maxLines: 4,
            style: const TextStyle(fontSize: 13, color: Color(0xFFE6EDF3)),
            decoration: const InputDecoration(
              border: OutlineInputBorder(),
              hintText:
                  'Describe how Antigravity CLI should solve this comment...',
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
            child: const Text('Save & Queue Fix'),
          ),
        ],
      ),
    );
    ctrl.dispose();
    if (custom != null && custom.isNotEmpty) {
      await widget.onThreadAction('selectSolution', <String, dynamic>{
        'optionId': 'custom',
        'customText': custom,
        'queueForFix': true,
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final ReviewThread t = widget.thread;
    final bool isGh = t.isGithubExisting;
    final bool isAddressedGh =
        isGh && t.triageStatus == 'addressed_or_refuted';

    final ({String label, Color color}) pill = _resolveBadge(t);
    final List<ThreadMessage> extraMsgs = t.messages
        .skip(1)
        .where((ThreadMessage m) => !(isGh && m.isTriageNote))
        .toList();

    return AnimatedContainer(
      duration: const Duration(milliseconds: 220),
      padding: EdgeInsets.all(widget.compact ? 11 : 13),
      decoration: BoxDecoration(
        color: widget.highlighted
            ? const Color(0xFF162235)
            : const Color(0xFF131820),
        borderRadius: BorderRadius.circular(9),
        border: Border.all(
          color: widget.highlighted
              ? const Color(0xFF58A6FF)
              : const Color(0xFF30363D),
          width: widget.highlighted ? 1.5 : 1.0,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          // Header Row
          Wrap(
            spacing: 8,
            runSpacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: <Widget>[
              _PillBadge(label: pill.label, color: pill.color),
              if (t.isStaged && !isGh)
                const _PillBadge(
                  label: '☁️ Staged for GitHub',
                  color: Color(0xFF388BFD),
                ),
              if (isGh && t.author.isNotEmpty)
                _PillBadge(
                  label: '💬 @${t.author.replaceFirst('@', '')}',
                  color: const Color(0xFF8B949E),
                ),
              Text(
                t.cleanTitle,
                style: const TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                  color: Color(0xFFE6EDF3),
                ),
              ),
              InkWell(
                onTap: widget.onOpenFileAtLine,
                borderRadius: BorderRadius.circular(4),
                child: Padding(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
                  child: Text(
                    '${t.shortFileName}:L${t.startLine}',
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 11.5,
                      color: Color(0xFF58A6FF),
                      decoration: TextDecoration.underline,
                    ),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),

          // Original comment / draft comment
          if (!isGh && t.whyItMatters.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: InlineMarkdownText(
                text: '**Context:** ${t.whyItMatters}',
                fontSize: 12,
                color: const Color(0xFF8B949E),
              ),
            ),
          if (t.draftComment.isNotEmpty)
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: const Color(0xFF0D1117),
                borderRadius: BorderRadius.circular(7),
                border: Border.all(color: const Color(0xFF21262D)),
              ),
              child: RichMarkdownBlock(
                text: t.draftComment,
                fontSize: 12.5,
              ),
            ),

          // Structured Quasar Follow-Up on EVERY GitHub comment
          if (isGh && !t.isFixed) ...<Widget>[
            const SizedBox(height: 8),
            _QuasarFollowUpBox(thread: t),
          ],

          // Uncertain preference calibration prompt
          if (t.isUncertain) ...<Widget>[
            const SizedBox(height: 8),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: const Color(0xFF2A200D),
                borderRadius: BorderRadius.circular(7),
                border: Border.all(
                  color: const Color(0xFFD29922).withValues(alpha: 0.45),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  if (t.uncertaintyQuestion.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: InlineMarkdownText(
                        text:
                            '🤔 **Calibrate Preference:** ${t.uncertaintyQuestion}',
                        fontSize: 12,
                        color: const Color(0xFFE3B341),
                      ),
                    ),
                  Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children: <Widget>[
                      _SmallActionBtn(
                        label: '✅ Stage & Always Flag Similar',
                        primary: true,
                        onTap: () => widget.onThreadAction(
                          'calibrate',
                          <String, dynamic>{'decision': 'keep_always'},
                        ),
                      ),
                      _SmallActionBtn(
                        label: 'Keep Once',
                        onTap: () => widget.onThreadAction(
                          'calibrate',
                          <String, dynamic>{'decision': 'keep_once'},
                        ),
                      ),
                      _SmallActionBtn(
                        label: '🚫 Dismiss & Skip in Future',
                        onTap: () => widget.onThreadAction(
                          'calibrate',
                          <String, dynamic>{'decision': 'dismiss_skip'},
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],

          // Solution Options selector
          if (widget.isOwnedReview &&
              !t.isFixed &&
              (t.solutionOptions.length >= 2 ||
                  t.selectedSolution.isNotEmpty)) ...<Widget>[
            const SizedBox(height: 8),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: const Color(0xFF0E1D28),
                borderRadius: BorderRadius.circular(7),
                border: Border.all(
                  color: const Color(0xFF388BFD).withValues(alpha: 0.35),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Row(
                    children: <Widget>[
                      const Expanded(
                        child: Text(
                          '🧭 Choose How to Solve This:',
                          style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w700,
                            color: Color(0xFF79C0FF),
                          ),
                        ),
                      ),
                      if (t.selectedSolution.isNotEmpty)
                        TextButton(
                          onPressed: () => widget.onThreadAction(
                            'selectSolution',
                            const <String, dynamic>{'optionId': 'clear'},
                          ),
                          style: TextButton.styleFrom(
                            visualDensity: VisualDensity.compact,
                            padding: const EdgeInsets.symmetric(horizontal: 6),
                          ),
                          child: const Text(
                            'Clear',
                            style: TextStyle(fontSize: 11),
                          ),
                        ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  for (final SolutionOption opt in t.solutionOptions)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: InkWell(
                        onTap: () => widget.onThreadAction(
                          'selectSolution',
                          <String, dynamic>{'optionId': opt.id},
                        ),
                        borderRadius: BorderRadius.circular(6),
                        child: Container(
                          width: double.infinity,
                          padding: const EdgeInsets.symmetric(
                            horizontal: 9,
                            vertical: 6,
                          ),
                          decoration: BoxDecoration(
                            color: t.selectedSolutionId == opt.id
                                ? const Color(0xFF1F3A56)
                                : const Color(0xFF0D1117),
                            borderRadius: BorderRadius.circular(6),
                            border: Border.all(
                              color: t.selectedSolutionId == opt.id
                                  ? const Color(0xFF58A6FF)
                                  : const Color(0xFF30363D),
                            ),
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: <Widget>[
                              Text(
                                '${t.selectedSolutionId == opt.id ? '✅ ' : '○ '}${opt.label}${opt.recommended ? ' (Recommended)' : ''}',
                                style: const TextStyle(
                                  fontSize: 12,
                                  fontWeight: FontWeight.w600,
                                  color: Color(0xFFE6EDF3),
                                ),
                              ),
                              if (opt.description.isNotEmpty)
                                Padding(
                                  padding: const EdgeInsets.only(top: 2),
                                  child: Text(
                                    opt.description,
                                    style: const TextStyle(
                                      fontSize: 11,
                                      color: Color(0xFF8B949E),
                                    ),
                                  ),
                                ),
                            ],
                          ),
                        ),
                      ),
                    ),
                  if (t.selectedSolutionId == 'custom' &&
                      t.selectedSolution.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: Text(
                        '✅ Custom approach: ${t.selectedSolution}',
                        style: const TextStyle(
                          fontSize: 11.5,
                          color: Color(0xFF79C0FF),
                        ),
                      ),
                    ),
                  _SmallActionBtn(
                    label: '✏️ Custom Approach...',
                    onTap: _showCustomSolutionDialog,
                  ),
                ],
              ),
            ),
          ],

          // Thread Replies & Live Q&A History
          if (extraMsgs.isNotEmpty) ...<Widget>[
            const SizedBox(height: 8),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.only(left: 10),
              decoration: const BoxDecoration(
                border: Border(
                  left: BorderSide(color: Color(0xFF30363D), width: 2),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  for (final ThreadMessage m in extraMsgs)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Text(
                            m.author,
                            style: TextStyle(
                              fontSize: 11,
                              fontWeight: FontWeight.w700,
                              color: m.author == 'You'
                                  ? const Color(0xFF79C0FF)
                                  : const Color(0xFF3FB950),
                            ),
                          ),
                          const SizedBox(height: 2),
                          RichMarkdownBlock(
                            text: m.body,
                            fontSize: 12,
                            color: m.pending
                                ? const Color(0xFF8B949E)
                                : const Color(0xFFE6EDF3),
                          ),
                        ],
                      ),
                    ),
                ],
              ),
            ),
          ],

          // Action Bar (attached cleanly at the bottom of the card, after Quasar's Follow-Up)
          const SizedBox(height: 8),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: <Widget>[
              if (!t.isFixed && (!isAddressedGh || t.selectedForFix))
                _SmallActionBtn(
                  label: t.selectedForFix ? '🔧 Queued for Fix ✓' : '🔧 Queue Fix',
                  primary: t.selectedForFix,
                  onTap: () => widget.onThreadAction('toggleFix'),
                )
              else if (isAddressedGh && !t.isFixed)
                const Padding(
                  padding: EdgeInsets.only(right: 4),
                  child: Text(
                    '✅ No code changes needed',
                    style: TextStyle(
                      fontSize: 11.5,
                      fontWeight: FontWeight.w600,
                      color: Color(0xFF3FB950),
                    ),
                  ),
                ),
              _SmallActionBtn(
                label: '💬 Ask / Reply',
                onTap: () => setState(() {
                  _changeMode = false;
                  _replyOpen = !_replyOpen;
                }),
              ),
              if (!isGh && !t.isFixed) ...<Widget>[
                _SmallActionBtn(
                  label: '✨ Ask Agent to Revise',
                  onTap: () => setState(() {
                    _changeMode = true;
                    _replyOpen = true;
                  }),
                ),
                _SmallActionBtn(
                  label: '✏️ Edit',
                  onTap: _showEditDraftDialog,
                ),
                _SmallActionBtn(
                  label: t.isStaged ? 'Unstage' : '☁️ Stage',
                  primary: t.isStaged,
                  onTap: () => widget.onThreadAction('toggleStage'),
                ),
              ],
              if (widget.onOpenInExternalEditor != null)
                _SmallActionBtn(
                  label: '↗ Editor',
                  onTap: widget.onOpenInExternalEditor!,
                ),
              _SmallActionBtn(
                label: '👍',
                onTap: () => widget.onThreadAction(
                  'rate',
                  const <String, dynamic>{'rating': 'up'},
                ),
              ),
              _SmallActionBtn(
                label: '✕ Dismiss',
                onTap: () => widget.onThreadAction('dismiss'),
              ),
            ],
          ),

          // In-Place Reply Box (zero scroll jump!)
          if (_replyOpen) ...<Widget>[
            const SizedBox(height: 8),
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: <Widget>[
                Expanded(
                  child: TextField(
                    controller: _replyCtrl,
                    autofocus: true,
                    minLines: 1,
                    maxLines: 4,
                    onSubmitted: (_) => _submitReply(),
                    style: const TextStyle(
                      fontSize: 12.5,
                      color: Color(0xFFE6EDF3),
                    ),
                    decoration: InputDecoration(
                      isDense: true,
                      filled: true,
                      fillColor: const Color(0xFF0D1117),
                      hintText: _changeMode
                          ? 'Tell Antigravity CLI how to revise this comment...'
                          : 'Ask Antigravity CLI about this code or comment...',
                      hintStyle: const TextStyle(
                        fontSize: 12,
                        color: Color(0xFF8B949E),
                      ),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(6),
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                FilledButton(
                  onPressed: _submittingReply ? null : _submitReply,
                  child: Text(_submittingReply ? 'Sending...' : 'Send'),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  ({String label, Color color}) _resolveBadge(ReviewThread t) {
    if (t.isFixed) {
      return (label: '✅ Fixed', color: const Color(0xFF3FB950));
    }
    if (t.isUncertain) {
      return (label: '🤔 Uncertain', color: const Color(0xFFD29922));
    }
    if (t.isGithubExisting) {
      final String badge =
          t.triageBadge.isNotEmpty ? t.triageBadge : '💬 GitHub Comment';
      if (t.triageStatus == 'addressed_or_refuted') {
        return (label: badge, color: const Color(0xFF3FB950));
      }
      if (t.triageStatus == 'partially_addressed') {
        return (label: badge, color: const Color(0xFFD29922));
      }
      if (t.triageStatus == 'recommended_fix') {
        return (label: badge, color: const Color(0xFFF0883E));
      }
      return (label: badge, color: const Color(0xFFA371F7));
    }
    final String sev = t.severity.toUpperCase();
    if (sev.contains('BLOCK')) {
      return (label: '🔴 Blocking', color: const Color(0xFFF85149));
    }
    if (sev.contains('DESIGN')) {
      return (label: '🟡 Design', color: const Color(0xFFD29922));
    }
    if (sev.contains('QA')) {
      return (label: '💬 Q&A', color: const Color(0xFF58A6FF));
    }
    return (label: '🟢 Nit', color: const Color(0xFF3FB950));
  }
}

class _QuasarFollowUpBox extends StatelessWidget {
  const _QuasarFollowUpBox({required this.thread});

  final ReviewThread thread;

  @override
  Widget build(BuildContext context) {
    final Color accent = thread.triageStatus == 'addressed_or_refuted'
        ? const Color(0xFF3FB950)
        : thread.triageStatus == 'partially_addressed'
            ? const Color(0xFFD29922)
            : const Color(0xFF58A6FF);

    final String heading = thread.triageLabel.isNotEmpty
        ? '🤖 Quasar Follow-Up — ${thread.triageLabel}'
        : '🤖 Quasar Follow-Up';

    return ClipRRect(
      borderRadius: BorderRadius.circular(7),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: accent.withValues(alpha: 0.08),
          border: Border(
            left: BorderSide(color: accent, width: 3),
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              heading,
              style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w700,
                color: accent,
              ),
            ),
            const SizedBox(height: 5),
            InlineMarkdownText(
              text: "✅ **What's Done:** ${thread.effectiveWhatIsDone}",
              fontSize: 12,
            ),
            const SizedBox(height: 3),
            InlineMarkdownText(
              text: "⚠️ **What Isn't Done:** ${thread.effectiveWhatIsNotDone}",
              fontSize: 12,
            ),
            const SizedBox(height: 3),
            InlineMarkdownText(
              text:
                  '👉 **What Should Be Done:** ${thread.effectiveWhatShouldBeDone}',
              fontSize: 12,
            ),
          ],
        ),
      ),
    );
  }
}

class _PillBadge extends StatelessWidget {
  const _PillBadge({required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2.5),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.45)),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w600,
          color: color,
        ),
      ),
    );
  }
}

class _SmallActionBtn extends StatelessWidget {
  const _SmallActionBtn({
    required this.label,
    required this.onTap,
    this.primary = false,
  });

  final String label;
  final VoidCallback onTap;
  final bool primary;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: primary ? const Color(0xFF1F6FEB) : const Color(0xFF21262D),
      borderRadius: BorderRadius.circular(6),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(6),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 8.5, vertical: 4.5),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(6),
            border: Border.all(
              color:
                  primary ? const Color(0xFF388BFD) : const Color(0xFF30363D),
            ),
          ),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 11.5,
              fontWeight: FontWeight.w600,
              color:
                  primary ? Colors.white : const Color(0xFFC9D1D9),
            ),
          ),
        ),
      ),
    );
  }
}
