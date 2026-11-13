class QuasarStatePayload {
  const QuasarStatePayload({
    required this.activeSession,
    required this.filesDiff,
    required this.sessions,
    required this.githubAuth,
    required this.defaultWorkspaceDir,
    required this.agentStatus,
    required this.sidecarPort,
  });

  factory QuasarStatePayload.fromJson(Map<String, dynamic> json) {
    final Object? rawActive = json['activeSession'];
    final List<dynamic> rawDiffs =
        (json['filesDiff'] as List<dynamic>?) ?? const <dynamic>[];
    final List<dynamic> rawSessions =
        (json['sessions'] as List<dynamic>?) ?? const <dynamic>[];
    final Map<String, dynamic> rawAuth =
        (json['githubAuth'] as Map<String, dynamic>?) ??
            const <String, dynamic>{};
    final Map<String, dynamic> rawAgent =
        (json['agentStatus'] as Map<String, dynamic>?) ??
            const <String, dynamic>{};

    return QuasarStatePayload(
      activeSession: rawActive is Map<String, dynamic>
          ? ReviewSession.fromJson(rawActive)
          : null,
      filesDiff: rawDiffs
          .whereType<Map<String, dynamic>>()
          .map(FileDiff.fromJson)
          .toList(),
      sessions: rawSessions
          .whereType<Map<String, dynamic>>()
          .map(SessionSummary.fromJson)
          .toList(),
      githubAuth: GithubAuthInfo.fromJson(rawAuth),
      defaultWorkspaceDir: '${json['defaultWorkspaceDir'] ?? ''}',
      agentStatus: AgentLiveStatus.fromJson(rawAgent),
      sidecarPort: (json['sidecarPort'] as num?)?.toInt() ?? 49341,
    );
  }

  final ReviewSession? activeSession;
  final List<FileDiff> filesDiff;
  final List<SessionSummary> sessions;
  final GithubAuthInfo githubAuth;
  final String defaultWorkspaceDir;
  final AgentLiveStatus agentStatus;
  final int sidecarPort;
}

class SessionSummary {
  const SessionSummary({
    required this.id,
    required this.sessionId,
    required this.title,
    required this.repo,
    required this.updatedAt,
    required this.stopsRead,
    required this.stopsTotal,
    required this.threadsCount,
    required this.effortLevel,
    required this.commentsOnly,
    required this.isTutorial,
  });

  factory SessionSummary.fromJson(Map<String, dynamic> json) {
    return SessionSummary(
      id: '${json['id'] ?? ''}',
      sessionId: '${json['sessionId'] ?? json['id'] ?? ''}',
      title: '${json['title'] ?? 'Review Session'}',
      repo: '${json['repo'] ?? ''}',
      updatedAt: '${json['updatedAt'] ?? ''}',
      stopsRead: (json['stopsRead'] as num?)?.toInt() ?? 0,
      stopsTotal: (json['stopsTotal'] as num?)?.toInt() ?? 0,
      threadsCount: (json['threadsCount'] as num?)?.toInt() ?? 0,
      effortLevel: '${json['effortLevel'] ?? 'high'}',
      commentsOnly: json['commentsOnly'] == true,
      isTutorial: json['isTutorial'] == true,
    );
  }

  final String id;
  final String sessionId;
  final String title;
  final String repo;
  final String updatedAt;
  final int stopsRead;
  final int stopsTotal;
  final int threadsCount;
  final String effortLevel;
  final bool commentsOnly;
  final bool isTutorial;
}

class GithubAuthInfo {
  const GithubAuthInfo({
    required this.configured,
    required this.login,
    required this.tokenPreview,
  });

  factory GithubAuthInfo.fromJson(Map<String, dynamic> json) {
    return GithubAuthInfo(
      configured: json['configured'] == true,
      login: '${json['login'] ?? ''}',
      tokenPreview: '${json['tokenPreview'] ?? ''}',
    );
  }

  final bool configured;
  final String login;
  final String tokenPreview;
}

class AgentLiveStatus {
  const AgentLiveStatus({
    required this.active,
    required this.completedJustNow,
    required this.title,
    required this.stage,
    required this.detail,
    required this.percent,
    required this.elapsedSeconds,
    required this.subagents,
  });

  factory AgentLiveStatus.fromJson(Map<String, dynamic> json) {
    final List<dynamic> rawSubs =
        (json['subagents'] as List<dynamic>?) ?? const <dynamic>[];
    return AgentLiveStatus(
      active: json['active'] == true,
      completedJustNow: json['completedJustNow'] == true,
      title: '${json['title'] ?? ''}',
      stage: '${json['stage'] ?? ''}',
      detail: '${json['detail'] ?? ''}',
      percent: (json['percent'] as num?)?.toInt() ?? 0,
      elapsedSeconds: (json['elapsedSeconds'] as num?)?.toInt() ?? 0,
      subagents: rawSubs.map((dynamic e) => '$e').toList(),
    );
  }

  final bool active;
  final bool completedJustNow;
  final String title;
  final String stage;
  final String detail;
  final int percent;
  final int elapsedSeconds;
  final List<String> subagents;
}

class ReviewSession {
  const ReviewSession({
    required this.sessionId,
    required this.pr,
    required this.effortLevel,
    required this.commentsOnly,
    required this.analysisStatus,
    required this.cliffsNotes,
    required this.chapters,
    required this.threads,
    required this.coverageMatrix,
    required this.dismissedChecks,
    required this.topLevelComment,
    required this.repoDir,
    required this.workspaceDir,
    required this.conversationId,
    required this.isTutorial,
  });

  factory ReviewSession.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic> rawPr =
        (json['pr'] as Map<String, dynamic>?) ?? const <String, dynamic>{};
    final Map<String, dynamic> rawCn =
        (json['cliffsNotes'] as Map<String, dynamic>?) ??
            const <String, dynamic>{};
    final List<dynamic> rawChapters =
        (json['chapters'] as List<dynamic>?) ?? const <dynamic>[];
    final List<dynamic> rawThreads =
        (json['threads'] as List<dynamic>?) ?? const <dynamic>[];
    final List<dynamic> rawCov =
        (json['coverageMatrix'] as List<dynamic>?) ?? const <dynamic>[];
    final List<dynamic> rawDismissed =
        (json['dismissedChecks'] as List<dynamic>?) ?? const <dynamic>[];

    return ReviewSession(
      sessionId: '${json['sessionId'] ?? ''}',
      pr: PrMetadata.fromJson(rawPr),
      effortLevel: '${json['effortLevel'] ?? 'high'}',
      commentsOnly: json['commentsOnly'] == true,
      analysisStatus: '${json['analysisStatus'] ?? 'ready'}',
      cliffsNotes: CliffsNotesData.fromJson(rawCn),
      chapters: rawChapters
          .whereType<Map<String, dynamic>>()
          .map(ReviewChapter.fromJson)
          .toList(),
      threads: rawThreads
          .whereType<Map<String, dynamic>>()
          .map(ReviewThread.fromJson)
          .toList(),
      coverageMatrix: rawCov
          .whereType<Map<String, dynamic>>()
          .map(CoverageRow.fromJson)
          .toList(),
      dismissedChecks: rawDismissed.map((dynamic e) => '$e').toList(),
      topLevelComment: '${json['topLevelComment'] ?? ''}',
      repoDir: '${json['repoDir'] ?? ''}',
      workspaceDir: '${json['workspaceDir'] ?? ''}',
      conversationId: '${json['conversationId'] ?? ''}',
      isTutorial: json['isTutorial'] == true || rawPr['isTutorial'] == true,
    );
  }

  final String sessionId;
  final PrMetadata pr;
  final String effortLevel;
  final bool commentsOnly;
  final String analysisStatus;
  final CliffsNotesData cliffsNotes;
  final List<ReviewChapter> chapters;
  final List<ReviewThread> threads;
  final List<CoverageRow> coverageMatrix;
  final List<String> dismissedChecks;
  final String topLevelComment;
  final String repoDir;
  final String workspaceDir;
  final String conversationId;
  final bool isTutorial;

  List<ReviewThread> get activeThreads =>
      threads.where((ReviewThread t) => t.status != 'dismissed').toList();

  List<TourStop> get allStops => <TourStop>[
        for (final ReviewChapter ch in chapters) ...ch.stops,
      ];
}

class PrMetadata {
  const PrMetadata({
    required this.owner,
    required this.repo,
    required this.number,
    required this.title,
    required this.url,
    required this.author,
    required this.baseRefName,
    required this.headRefName,
    required this.additions,
    required this.deletions,
    required this.changedFilesCount,
    required this.isOwnedByUser,
  });

  factory PrMetadata.fromJson(Map<String, dynamic> json) {
    return PrMetadata(
      owner: '${json['owner'] ?? ''}',
      repo: '${json['repo'] ?? ''}',
      number: (json['number'] as num?)?.toInt(),
      title: '${json['title'] ?? 'Code Review'}',
      url: '${json['url'] ?? ''}',
      author: '${json['author'] ?? 'local'}',
      baseRefName: '${json['baseRefName'] ?? 'main'}',
      headRefName: '${json['headRefName'] ?? json['headBranch'] ?? 'HEAD'}',
      additions: (json['additions'] as num?)?.toInt() ?? 0,
      deletions: (json['deletions'] as num?)?.toInt() ?? 0,
      changedFilesCount: (json['changedFilesCount'] as num?)?.toInt() ?? 0,
      isOwnedByUser: json['isOwnedByUser'] != false,
    );
  }

  final String owner;
  final String repo;
  final int? number;
  final String title;
  final String url;
  final String author;
  final String baseRefName;
  final String headRefName;
  final int additions;
  final int deletions;
  final int changedFilesCount;
  final bool isOwnedByUser;
}

class CliffsNotesData {
  const CliffsNotesData({
    required this.tldr,
    required this.whatAndWhy,
    required this.architectureDiagram,
    required this.keySymbols,
    required this.designTradeoffs,
  });

  factory CliffsNotesData.fromJson(Map<String, dynamic> json) {
    final List<dynamic> rawSyms =
        (json['keySymbols'] as List<dynamic>?) ?? const <dynamic>[];
    final List<dynamic> rawTradeoffs =
        (json['designTradeoffs'] as List<dynamic>?) ?? const <dynamic>[];
    return CliffsNotesData(
      tldr: '${json['tldr'] ?? ''}',
      whatAndWhy: '${json['whatAndWhy'] ?? ''}',
      architectureDiagram: '${json['architectureDiagram'] ?? ''}',
      keySymbols: rawSyms
          .whereType<Map<String, dynamic>>()
          .map(KeySymbol.fromJson)
          .toList(),
      designTradeoffs: rawTradeoffs.map((dynamic e) => '$e').toList(),
    );
  }

  final String tldr;
  final String whatAndWhy;
  final String architectureDiagram;
  final List<KeySymbol> keySymbols;
  final List<String> designTradeoffs;
}

class KeySymbol {
  const KeySymbol({required this.name, required this.role});

  factory KeySymbol.fromJson(Map<String, dynamic> json) {
    return KeySymbol(
      name: '${json['name'] ?? ''}',
      role: '${json['role'] ?? ''}',
    );
  }

  final String name;
  final String role;
}

class ReviewChapter {
  const ReviewChapter({
    required this.id,
    required this.title,
    required this.summary,
    required this.cliffsNotesBullets,
    required this.stops,
  });

  factory ReviewChapter.fromJson(Map<String, dynamic> json) {
    final List<dynamic> rawBullets =
        (json['cliffsNotesBullets'] as List<dynamic>?) ?? const <dynamic>[];
    final List<dynamic> rawStops =
        (json['stops'] as List<dynamic>?) ?? const <dynamic>[];
    return ReviewChapter(
      id: '${json['id'] ?? ''}',
      title: '${json['title'] ?? 'Chapter'}',
      summary: '${json['summary'] ?? ''}',
      cliffsNotesBullets: rawBullets.map((dynamic e) => '$e').toList(),
      stops: rawStops
          .whereType<Map<String, dynamic>>()
          .map(TourStop.fromJson)
          .toList(),
    );
  }

  final String id;
  final String title;
  final String summary;
  final List<String> cliffsNotesBullets;
  final List<TourStop> stops;
}

class TourStop {
  const TourStop({
    required this.id,
    required this.stopNumber,
    required this.title,
    required this.path,
    required this.startLine,
    required this.endLine,
    required this.cliffsNotes,
    required this.whyItChanged,
    required this.callersAndInvariants,
    required this.read,
  });

  factory TourStop.fromJson(Map<String, dynamic> json) {
    final int start = (json['startLine'] as num?)?.toInt() ?? 1;
    return TourStop(
      id: '${json['id'] ?? ''}',
      stopNumber: '${json['stopNumber'] ?? ''}',
      title: '${json['title'] ?? ''}',
      path: '${json['path'] ?? ''}',
      startLine: start,
      endLine: (json['endLine'] as num?)?.toInt() ?? start,
      cliffsNotes: '${json['cliffsNotes'] ?? json['description'] ?? ''}',
      whyItChanged: '${json['whyItChanged'] ?? ''}',
      callersAndInvariants: '${json['callersAndInvariants'] ?? ''}',
      read: json['read'] == true,
    );
  }

  final String id;
  final String stopNumber;
  final String title;
  final String path;
  final int startLine;
  final int endLine;
  final String cliffsNotes;
  final String whyItChanged;
  final String callersAndInvariants;
  final bool read;
}

class ReviewThread {
  const ReviewThread({
    required this.id,
    required this.kind,
    required this.severity,
    required this.status,
    required this.sourceAgent,
    required this.sourceAgents,
    required this.path,
    required this.targetFile,
    required this.isTopLevel,
    required this.startLine,
    required this.endLine,
    required this.title,
    required this.whyItMatters,
    required this.draftComment,
    required this.author,
    required this.triageStatus,
    required this.triageCategory,
    required this.triageBadge,
    required this.triageLabel,
    required this.triageSummary,
    required this.whatIsDone,
    required this.whatIsNotDone,
    required this.whatShouldBeDone,
    required this.recommendedForFix,
    required this.selectedForFix,
    required this.fixStatus,
    required this.fixedSummary,
    required this.uncertaintyQuestion,
    required this.solutionOptions,
    required this.selectedSolutionId,
    required this.selectedSolution,
    required this.messages,
  });

  factory ReviewThread.fromJson(Map<String, dynamic> json) {
    final int start = (json['startLine'] as num?)?.toInt() ?? 1;
    final List<dynamic> rawOpts =
        (json['solutionOptions'] as List<dynamic>?) ?? const <dynamic>[];
    final List<dynamic> rawMsgs =
        (json['messages'] as List<dynamic>?) ?? const <dynamic>[];
    final List<dynamic> rawSrcAgents =
        (json['sourceAgents'] as List<dynamic>?) ?? const <dynamic>[];
    final List<String> parsedSrcAgents = rawSrcAgents
        .map((dynamic e) => '$e'.trim())
        .where((String s) => s.isNotEmpty)
        .toList();
    final String singleSrc = '${json['sourceAgent'] ?? ''}'.trim();
    if (singleSrc.isNotEmpty && !parsedSrcAgents.contains(singleSrc)) {
      parsedSrcAgents.insert(0, singleSrc);
    }
    return ReviewThread(
      id: '${json['id'] ?? ''}',
      kind: '${json['kind'] ?? 'ai_candidate'}',
      severity: '${json['severity'] ?? 'DESIGN'}',
      status: '${json['status'] ?? 'candidate'}',
      sourceAgent: parsedSrcAgents.isNotEmpty ? parsedSrcAgents.first : '',
      sourceAgents: parsedSrcAgents,
      path: '${json['path'] ?? ''}',
      targetFile: '${json['targetFile'] ?? ''}',
      isTopLevel: json['isTopLevel'] == true,
      startLine: start,
      endLine: (json['endLine'] as num?)?.toInt() ?? start,
      title: '${json['title'] ?? 'Comment'}',
      whyItMatters: '${json['whyItMatters'] ?? ''}',
      draftComment: '${json['draftComment'] ?? json['body'] ?? ''}',
      author: '${json['author'] ?? ''}',
      triageStatus: '${json['triageStatus'] ?? ''}',
      triageCategory: '${json['triageCategory'] ?? ''}',
      triageBadge: '${json['triageBadge'] ?? ''}',
      triageLabel: '${json['triageLabel'] ?? ''}',
      triageSummary: '${json['triageSummary'] ?? ''}',
      whatIsDone: '${json['whatIsDone'] ?? ''}',
      whatIsNotDone: '${json['whatIsNotDone'] ?? ''}',
      whatShouldBeDone: '${json['whatShouldBeDone'] ?? ''}',
      recommendedForFix: json['recommendedForFix'] == true,
      selectedForFix: json['selectedForFix'] == true,
      fixStatus: '${json['fixStatus'] ?? ''}',
      fixedSummary: '${json['fixedSummary'] ?? ''}',
      uncertaintyQuestion:
          '${json['uncertaintyQuestion'] ?? json['calibrationQuestion'] ?? ''}',
      solutionOptions: rawOpts
          .whereType<Map<String, dynamic>>()
          .map(SolutionOption.fromJson)
          .toList(),
      selectedSolutionId: '${json['selectedSolutionId'] ?? ''}',
      selectedSolution: '${json['selectedSolution'] ?? ''}',
      messages: rawMsgs
          .whereType<Map<String, dynamic>>()
          .map(ThreadMessage.fromJson)
          .toList(),
    );
  }

  final String id;
  final String kind;
  final String severity;
  final String status;
  final String sourceAgent;
  final List<String> sourceAgents;
  final String path;
  final String targetFile;
  final bool isTopLevel;
  final int startLine;
  final int endLine;
  final String title;
  final String whyItMatters;
  final String draftComment;
  final String author;
  final String triageStatus;
  final String triageCategory;
  final String triageBadge;
  final String triageLabel;
  final String triageSummary;
  final String whatIsDone;
  final String whatIsNotDone;
  final String whatShouldBeDone;
  final bool recommendedForFix;
  final bool selectedForFix;
  final String fixStatus;
  final String fixedSummary;
  final String uncertaintyQuestion;
  final List<SolutionOption> solutionOptions;
  final String selectedSolutionId;
  final String selectedSolution;
  final List<ThreadMessage> messages;

  bool get isGithubExisting => kind == 'github_existing';
  bool get isStaged => status == 'staged';
  bool get isUncertain => status == 'uncertain';
  bool get isFixed => status == 'fixed';

  String get displayPath =>
      (targetFile.isNotEmpty && path == 'TOP_LEVEL_PR_COMMENTS.md')
          ? targetFile
          : path;

  String get shortFileName {
    final String p = displayPath;
    if (p.isEmpty) {
      return 'Top-Level';
    }
    final int slash = p.lastIndexOf('/');
    return slash >= 0 ? p.substring(slash + 1) : p;
  }

  String get cleanTitle {
    return title.replaceFirst(
      RegExp(r'^(?:🛠️|⚠️|✅|💬|❓|👀|🤔|🔴|🟡|🟢)\s*\[[^\]]+\]\s*'),
      '',
    );
  }

  String get effectiveWhatIsDone {
    if (whatIsDone.trim().isNotEmpty) {
      return whatIsDone.trim();
    }
    if (triageStatus == 'addressed_or_refuted') {
      return triageSummary.isNotEmpty
          ? triageSummary
          : 'Already addressed in current code or thread.';
    }
    return 'Checked against current HEAD snapshot.';
  }

  String get effectiveWhatIsNotDone {
    if (whatIsNotDone.trim().isNotEmpty) {
      return whatIsNotDone.trim();
    }
    if (triageStatus == 'addressed_or_refuted') {
      return 'None — comment has been addressed.';
    }
    return triageSummary.isNotEmpty
        ? triageSummary
        : 'Needs review or code update.';
  }

  String get effectiveWhatShouldBeDone {
    if (whatShouldBeDone.trim().isNotEmpty) {
      return whatShouldBeDone.trim();
    }
    if (triageStatus == 'addressed_or_refuted') {
      return 'No code changes needed.';
    }
    return 'Update the implementation or reply in the PR thread.';
  }
}

class SolutionOption {
  const SolutionOption({
    required this.id,
    required this.label,
    required this.description,
    required this.recommended,
  });

  factory SolutionOption.fromJson(Map<String, dynamic> json) {
    return SolutionOption(
      id: '${json['id'] ?? ''}',
      label: '${json['label'] ?? ''}',
      description: '${json['description'] ?? ''}',
      recommended: json['recommended'] == true,
    );
  }

  final String id;
  final String label;
  final String description;
  final bool recommended;
}

class ThreadMessage {
  const ThreadMessage({
    required this.author,
    required this.body,
    required this.timestamp,
    required this.isTriageNote,
    required this.pending,
  });

  factory ThreadMessage.fromJson(Map<String, dynamic> json) {
    return ThreadMessage(
      author: '${json['author'] ?? 'Quasar'}',
      body: '${json['body'] ?? ''}',
      timestamp: '${json['timestamp'] ?? ''}',
      isTriageNote: json['isTriageNote'] == true,
      pending: json['pending'] == true,
    );
  }

  final String author;
  final String body;
  final String timestamp;
  final bool isTriageNote;
  final bool pending;
}

class CoverageRow {
  const CoverageRow({
    required this.behavior,
    required this.status,
    required this.notes,
  });

  factory CoverageRow.fromJson(Map<String, dynamic> json) {
    return CoverageRow(
      behavior: '${json['behavior'] ?? ''}',
      status: '${json['status'] ?? ''}',
      notes: '${json['notes'] ?? ''}',
    );
  }

  final String behavior;
  final String status;
  final String notes;
}

class FileDiff {
  const FileDiff({
    required this.path,
    required this.additions,
    required this.deletions,
    required this.rows,
  });

  factory FileDiff.fromJson(Map<String, dynamic> json) {
    final List<dynamic> rawRows =
        (json['rows'] as List<dynamic>?) ?? const <dynamic>[];
    return FileDiff(
      path: '${json['path'] ?? ''}',
      additions: (json['additions'] as num?)?.toInt() ?? 0,
      deletions: (json['deletions'] as num?)?.toInt() ?? 0,
      rows:
          rawRows.whereType<Map<String, dynamic>>().map(DiffRow.fromJson).toList(),
    );
  }

  final String path;
  final int additions;
  final int deletions;
  final List<DiffRow> rows;

  String get shortName {
    final int slash = path.lastIndexOf('/');
    return slash >= 0 ? path.substring(slash + 1) : path;
  }
}

class DiffRow {
  const DiffRow({
    required this.type,
    required this.baseLine,
    required this.headLine,
    required this.baseText,
    required this.headText,
  });

  factory DiffRow.fromJson(Map<String, dynamic> json) {
    return DiffRow(
      type: '${json['type'] ?? 'context'}'.trim(),
      baseLine: (json['baseLine'] as num?)?.toInt(),
      headLine: (json['headLine'] as num?)?.toInt(),
      baseText: '${json['baseText'] ?? ''}',
      headText: '${json['headText'] ?? ''}',
    );
  }

  final String type;
  final int? baseLine;
  final int? headLine;
  final String baseText;
  final String headText;
}

class FlowchartNode {
  const FlowchartNode({
    required this.id,
    required this.label,
    this.group,
  });

  final String id;
  final String label;
  final String? group;
}

class FlowchartEdge {
  const FlowchartEdge({
    required this.from,
    required this.to,
    this.label = '',
    this.dashed = false,
  });

  final String from;
  final String to;
  final String label;
  final bool dashed;
}

class FlowchartGraph {
  const FlowchartGraph({
    required this.nodes,
    required this.edges,
    required this.isTopDown,
  });

  final List<FlowchartNode> nodes;
  final List<FlowchartEdge> edges;
  final bool isTopDown;
}

FlowchartGraph parseMermaidFlowchart(String raw) {
  final Map<String, FlowchartNode> nodeMap = <String, FlowchartNode>{};
  final List<FlowchartEdge> edges = <FlowchartEdge>[];
  bool isTopDown = false;
  String? currentGroup;

  void ensureNode(String rawToken) {
    final String token = rawToken.trim();
    if (token.isEmpty) {
      return;
    }
    final RegExpMatch? m = RegExp(
      r'^([A-Za-z0-9_.-]+)\s*(?:\[\s*"?([^"\]]+)"?\s*\]|\(\s*"?([^"\)]+)"?\s*\)|\{\s*"?([^"\}]+)"?\s*\})?$',
    ).firstMatch(token);
    if (m == null) {
      return;
    }
    final String id = m.group(1)!;
    final String? parsedLabel = m.group(2) ?? m.group(3) ?? m.group(4);
    final FlowchartNode? existing = nodeMap[id];
    if (existing == null || (parsedLabel != null && existing.label == id)) {
      nodeMap[id] = FlowchartNode(
        id: id,
        label: (parsedLabel ?? existing?.label ?? id).trim(),
        group: currentGroup ?? existing?.group,
      );
    }
  }

  String extractId(String rawToken) {
    final RegExpMatch? m =
        RegExp(r'^([A-Za-z0-9_.-]+)').firstMatch(rawToken.trim());
    return m?.group(1) ?? rawToken.trim();
  }

  for (final String rawLine in raw.split('\n')) {
    final String line = rawLine.trim();
    if (line.isEmpty || line.startsWith('%%') || line.startsWith('```')) {
      continue;
    }
    if (line.startsWith('flowchart ') || line.startsWith('graph ')) {
      if (line.contains('TD') || line.contains('TB')) {
        isTopDown = true;
      }
      continue;
    }
    if (line.startsWith('subgraph ')) {
      final RegExpMatch? sm = RegExp(
        r'^subgraph\s+(?:[A-Za-z0-9_.-]+\s*\[\s*"?([^"\]]+)"?\s*\]|"?([^"]+)"?)$',
      ).firstMatch(line);
      currentGroup = (sm?.group(1) ?? sm?.group(2) ?? '')
          .replaceFirst('subgraph ', '')
          .trim();
      continue;
    }
    if (line == 'end') {
      currentGroup = null;
      continue;
    }
    if (line.startsWith('classDef ') ||
        line.startsWith('class ') ||
        line.startsWith('style ')) {
      continue;
    }

    final RegExp edgeRe = RegExp(
      r'^(.+?)\s*(-\.->|-->|==>|---)\s*(?:\|([^|]*)\|\s*)?(.+)$',
    );
    final RegExpMatch? em = edgeRe.firstMatch(line);
    if (em != null) {
      final String left = em.group(1)!.trim();
      final String arrow = em.group(2)!;
      final String edgeLabel = (em.group(3) ?? '').trim();
      final String right = em.group(4)!.trim();
      ensureNode(left);
      ensureNode(right);
      final String fromId = extractId(left);
      final String toId = extractId(right);
      if (fromId.isNotEmpty && toId.isNotEmpty) {
        edges.add(
          FlowchartEdge(
            from: fromId,
            to: toId,
            label: edgeLabel,
            dashed: arrow.contains('.'),
          ),
        );
      }
    } else {
      ensureNode(line);
    }
  }

  return FlowchartGraph(
    nodes: nodeMap.values.toList(),
    edges: edges,
    isTopDown: isTopDown,
  );
}
