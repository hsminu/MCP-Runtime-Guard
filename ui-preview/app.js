/* Browser-only interaction model. No MCP, Guard, registry, or approval API is called. */
const icons = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01" stroke-linecap="round"/>',
  check: '<path d="M20 7 10 17l-5-5" stroke-linecap="round" stroke-linejoin="round"/><path d="M12 2a10 10 0 1 0 10 10" stroke-linecap="round"/>',
  server: '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01" stroke-linecap="round"/>',
  shield: '<path d="M12 2 20 5v6c0 5.3-3.3 8.5-8 11-4.7-2.5-8-5.7-8-11V5l8-3Z"/><path d="m9 12 2 2 4-4" stroke-linecap="round" stroke-linejoin="round"/>',
  file: '<path d="M7 2h7l5 5v14a1 1 0 0 1-1 1H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z"/><path d="M14 2v6h5M8 13h8M8 17h8" stroke-linecap="round"/>',
  play: '<path d="m8 5 11 7-11 7V5Z" stroke-linejoin="round"/><circle cx="12" cy="12" r="10"/>',
  settings: '<path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z"/><path d="M19.4 15a8 8 0 0 0 .05-6l2-1.5-2-3.5-2.4 1a8 8 0 0 0-5-2L11.5.5h-4L7 3a8 8 0 0 0-3 2L1.5 4 0 7.5 2 9a8 8 0 0 0 0 6l-2 1.5L1.5 20 4 19a8 8 0 0 0 3 2l.5 2.5h4L12 21a8 8 0 0 0 5-2l2.4 1 2-3.5-2-1.5Z" transform="translate(1 -1) scale(.9)"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16" stroke-linecap="round"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/>',
  download: '<path d="M12 3v12m-4-4 4 4 4-4M4 17v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" stroke-linecap="round" stroke-linejoin="round"/>',
  search: '<circle cx="10.5" cy="10.5" r="7.5"/><path d="m16 16 5 5" stroke-linecap="round"/>',
  x: '<path d="M5 5 19 19M19 5 5 19" stroke-linecap="round"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 11v6M12 7h.01" stroke-linecap="round"/>',
  activity: '<path d="M3 12h4l3-7 4 14 3-7h4" stroke-linecap="round" stroke-linejoin="round"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2" stroke-linecap="round"/>',
  bolt: '<path d="m13 2-9 11h7l-1 9 10-12h-7l1-8Z" stroke-linejoin="round"/>'
};

function icon(name) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.info}</svg>`;
}

function escapeHTML(value) {
  return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
}

const calls = [
  { id: 'call-108', timestamp: '2026-10-12T14:32:08+09:00', server: 'github-issues', tool: 'create_issue', kind: 'SEND', status: 'allow', label: 'PUBLIC', task: 'task-024', duration: '184 ms', serverBoundary: 'approved_external', destinationBoundary: 'approved_external', destination: 'github.com / public-repo', reason: 'PUBLIC 자료이며 두 경계의 허용 등급 안에 있음', input: '제목과 공개 이슈 설명 · 1.2 KB', output: '이슈 번호와 URL · 0.3 KB' },
  { id: 'call-107', timestamp: '2026-10-12T14:28:42+09:00', server: 'slack-notify', tool: 'post_message', kind: 'SEND', status: 'ask', label: 'INTERNAL', task: 'task-023', duration: '—', serverBoundary: 'approved_external', destinationBoundary: 'approved_external', destination: 'workspace / #release-updates', reason: 'INTERNAL 자료가 승인된 외부 경계를 통과하므로 사람 승인 대기', input: '공지문 · 0.8 KB', output: '전송 전 · 결과 없음' },
  { id: 'call-106', timestamp: '2026-10-12T14:21:17+09:00', server: 'docs-local', tool: 'read_document', kind: 'READ', status: 'allow', label: 'INTERNAL', task: 'task-023', duration: '96 ms', serverBoundary: 'internal', destinationBoundary: '해당 없음', destination: '로컬 문서 저장소', reason: '내부 서버 경계에서 READ 허용 · 응답 등급 커밋 후 공개', input: '문서 ID · 0.1 KB', output: '본문 비공개 · 등급 INTERNAL' },
  { id: 'call-105', timestamp: '2026-10-12T14:17:03+09:00', server: 'web-search', tool: 'search_web', kind: 'READ', status: 'deny', label: 'CONFIDENTIAL', task: 'task-022', duration: '—', serverBoundary: 'general_external', destinationBoundary: '해당 없음', destination: '외부 MCP 서버', reason: 'CONFIDENTIAL 자료가 외부 서버 경계의 최대 등급을 초과함', input: '검색어 요약만 표시 · 원문 비공개', output: '실행 안 됨' },
  { id: 'call-104', timestamp: '2026-10-12T13:52:31+09:00', server: 'github-issues', tool: 'create_issue', kind: 'SEND', status: 'ask', label: 'INTERNAL', task: 'task-021', duration: '—', serverBoundary: 'approved_external', destinationBoundary: 'approved_external', destination: 'github.com / internal-repo', reason: 'INTERNAL 자료가 승인된 외부 경계를 통과하므로 사람 승인 대기', input: '이슈 초안 · 1.6 KB', output: '전송 전 · 결과 없음' },
  { id: 'call-103', timestamp: '2026-10-12T13:39:54+09:00', server: 'docs-local', tool: 'read_document', kind: 'READ', status: 'allow', label: 'CONFIDENTIAL', task: 'task-022', duration: '105 ms', serverBoundary: 'internal', destinationBoundary: '해당 없음', destination: '로컬 문서 저장소', reason: '내부 서버 경계에서 READ 허용 · 응답 등급 커밋 후 공개', input: '문서 ID · 0.1 KB', output: '본문 비공개 · 등급 CONFIDENTIAL' },
  { id: 'call-102', timestamp: '2026-10-12T13:25:11+09:00', server: 'slack-notify', tool: 'post_message', kind: 'SEND', status: 'unknown', label: 'PUBLIC', task: 'task-020', duration: '—', serverBoundary: 'approved_external', destinationBoundary: 'approved_external', destination: 'workspace / #ops', reason: '전송 후 응답을 확인하지 못함 · 수신측 확인 필요 · 자동 재전송 금지', input: '운영 알림 · 0.4 KB', output: '결과 불명 · 수신측 확인 필요' },
  { id: 'call-101', timestamp: '2026-10-12T13:10:04+09:00', server: 'web-search', tool: 'search_web', kind: 'READ', status: 'allow', label: 'PUBLIC', task: 'task-019', duration: '312 ms', serverBoundary: 'general_external', destinationBoundary: '해당 없음', destination: '외부 MCP 서버', reason: 'PUBLIC 자료는 등록된 외부 서버 경계에서 허용', input: '공개 검색어 · 0.2 KB', output: '검색 결과 요약 · 등급 PUBLIC' }
];

const approvals = [
  { id: 'ap-002', callId: 'call-107', server: 'slack-notify', tool: 'post_message', destination: 'workspace / #release-updates', label: 'INTERNAL', args: '{\n  "channel": "#release-updates",\n  "text": "이번 주 릴리스 점검 회의는 목요일 15시입니다."\n}', timestamp: '2026-10-12T14:28:42+09:00', status: 'pending' },
  { id: 'ap-001', callId: 'call-104', server: 'github-issues', tool: 'create_issue', destination: 'github.com / internal-repo', label: 'INTERNAL', args: '{\n  "repository": "internal-repo",\n  "title": "온보딩 문서 검토",\n  "body": "예시용 내부 이슈 초안입니다."\n}', timestamp: '2026-10-12T13:52:31+09:00', status: 'pending' }
];

const servers = [
  { id: 'docs-local', name: 'docs-local', short: 'DOC', color: 'green', transport: 'stdio', boundary: 'internal', maxLabel: 'CONFIDENTIAL', tools: 4, status: 'active', endpoint: 'C:/mcp/docs/.venv/Scripts/python.exe', command: 'C:/mcp/docs/.venv/Scripts/python.exe', args: ['-m', 'company_docs.server'], envAllow: ['LANG'], description: '로컬 문서 조회' },
  { id: 'github-issues', name: 'github-issues', short: 'GH', color: '', transport: 'Streamable HTTP', boundary: 'approved_external', maxLabel: 'INTERNAL', tools: 6, status: 'active', endpoint: 'https://mcp.example.com/github', description: '이슈 조회 및 생성' },
  { id: 'slack-notify', name: 'slack-notify', short: 'SL', color: 'orange', transport: 'Streamable HTTP', boundary: 'approved_external', maxLabel: 'INTERNAL', tools: 3, status: 'active', endpoint: 'https://mcp.example.com/slack', description: '채널 알림 전송' },
  { id: 'web-search', name: 'web-search', short: 'WEB', color: '', transport: 'Streamable HTTP', boundary: 'general_external', maxLabel: 'PUBLIC', tools: 2, status: 'active', endpoint: 'https://mcp.example.com/search', description: '공개 웹 검색' }
];

const boundaryDescriptions = {
  internal: '내부 · 관리자가 내부 신뢰 경계로 등록한 서버',
  approved_external: '승인된 외부 · 사전 검토된 외부 운영자 서버',
  general_external: '일반 외부 · 그 외 외부 운영자 서버'
};
function transportCode(server) { return server.transport === 'stdio' ? 'stdio' : 'streamable_http'; }
function registryBadge(server) {
  const code = server.status === 'active' ? 'ACTIVE' : server.status === 'blocked' ? 'BLOCKED' : 'PENDING';
  const text = { ACTIVE: '활성 등록', PENDING: '검토 대기', BLOCKED: '사용 차단' }[code];
  return `<span class="status ${server.status}">${code} · ${text}</span>`;
}
function serverConfig(server) {
  return { id: server.id, transport: transportCode(server),
    ...(server.transport === 'stdio' ? { command: server.command, args: server.args, env_allow: server.envAllow } : { url: server.endpoint }),
    tier: server.boundary, max_label: server.maxLabel };
}

const pageLabels = { overview: '현황', logs: '호출 로그', approvals: '승인 대기', servers: 'MCP 서버', policy: '정책 · 경계', audit: '감사 기록', runs: '에이전트 실행', settings: '설정' };
const statusLabels = { allow: '허용', ask: '승인 대기', deny: '차단', unknown: '결과 불명', active: '등록 ACTIVE · 세션 미연결', pending: '승인 대기', approved: '승인됨', rejected: '거절됨', scan: '검사 대기', blocked: '차단' };
const state = { page: 'overview', search: '', statusFilter: 'all', serverFilter: 'all', taskFilter: '', selectedCall: null, selectedApproval: null, approvalSearch: '', approvalView: 'pending', approvalServer: 'all', approvalSort: 'oldest', approvalPage: 1, mcpSearch: '', mcpTransport: 'all', mcpStatus: 'all', compact: false, modal: null };
const main = document.getElementById('main');
const modalRoot = document.getElementById('modal-root');
let toastTimer;
let modalTrigger;
const DEMO_NOW = new Date('2026-10-12T14:35:00+09:00');
const APPROVAL_PAGE_SIZE = 6;
const dateFormatter = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
function dateParts(timestamp) { return Object.fromEntries(dateFormatter.formatToParts(new Date(timestamp)).map(part => [part.type, part.value])); }
function formatDateTime(timestamp) {
  const p = dateParts(timestamp);
  return `${p.year}년 ${p.month}월 ${p.day}일 ${p.hour}:${p.minute}:${p.second} KST`;
}
function timeCell(timestamp) {
  const p = dateParts(timestamp);
  return `<time datetime="${escapeHTML(timestamp)}" title="${formatDateTime(timestamp)}"><span class="date-line">${p.year}년 ${p.month}월 ${p.day}일</span><span class="clock-line">${p.hour}:${p.minute}:${p.second} <small>KST</small></span></time>`;
}
function waitingMinutes(item) { return Math.max(0, Math.floor((DEMO_NOW - new Date(item.timestamp)) / 60000)); }
function waitingLabel(item) {
  const minutes = waitingMinutes(item);
  return minutes < 1 ? '1분 미만 대기' : minutes < 60 ? `${minutes}분 대기` : `${Math.floor(minutes / 60)}시간 ${minutes % 60}분 대기`;
}
function pendingApprovals() { return approvals.filter(item => item.status === 'pending').sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp)); }
function approvalFor(call) { return approvals.find(item => item.callId === call.id); }
function callStatus(call) { const approval = approvalFor(call); return approval && approval.status !== 'pending' ? approval.status : call.status; }
function sortedCalls() { return [...calls].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp)); }
function keyValues(entries) { return `<dl class="kv">${entries.map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join('')}</dl>`; }


document.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); });

function statusBadge(status) { return `<span class="status ${escapeHTML(status)}">${statusLabels[status] || escapeHTML(status)}</span>`; }
function labelChip(label) { return `<span class="label-chip ${label.toLowerCase()}">${escapeHTML(label)}</span>`; }
function serverSecurityBadge(server) {
  const levels = { PUBLIC: ['공개', 'public'], INTERNAL: ['내부', 'internal'], CONFIDENTIAL: ['기밀', 'confidential'] };
  const [label, tone] = levels[server.maxLabel] || ['미지정', 'unknown'];
  return `<span class="server-security ${tone}"><span class="server-security-caption">${icon('shield')} 보안 등급 · 최대 수신</span><span class="server-security-value"><b>${label}</b><code>${escapeHTML(server.maxLabel || '미제공')}</code></span></span>`;
}

function heading(kicker, title, description, actions = '') {
  return `<div class="page-heading"><div><span class="eyebrow">${kicker}</span><h1>${title}</h1><p>${description}</p></div>${actions ? `<div class="heading-actions">${actions}</div>` : ''}</div>`;
}

function filteredCalls() {
  const term = state.search.trim().toLocaleLowerCase();
  return sortedCalls().filter(call =>
    (state.statusFilter === 'all' || callStatus(call) === state.statusFilter) &&
    (state.serverFilter === 'all' || call.server === state.serverFilter) &&
    (!state.taskFilter || call.task === state.taskFilter) &&
    (!term || [call.id, call.server, call.tool, call.task, call.destination].some(value => value.toLocaleLowerCase().includes(term)))
  );
}

function callTable(items, compact = false) {
  if (!items.length) return `<div class="empty-state"><strong>조건에 맞는 호출이 없어</strong>검색어나 필터를 바꿔봐.<br><button class="button" type="button" data-action="clear-filters">필터 초기화</button></div>`;
  return `<div class="table-wrap"><table><thead><tr><th>호출 일시 · 한국 시간</th><th>MCP / 도구</th><th>종류</th><th>데이터 등급</th><th>상태</th>${compact ? '' : '<th>Task</th>'}<th><span class="sr-only">상세 정보</span></th></tr></thead><tbody>${items.map(call => `<tr class="call-row" data-action="select-call" data-id="${call.id}"><td class="time-cell">${timeCell(call.timestamp)}</td><td><button type="button" class="cell-button" data-action="select-call" data-id="${call.id}" aria-label="${escapeHTML(call.tool)} ${call.id} 상세 보기"><span class="call-name mono">${escapeHTML(call.tool)}</span><span class="call-sub">${escapeHTML(call.server)}</span></button></td><td class="mono">${call.kind}</td><td>${labelChip(call.label)}</td><td>${statusBadge(callStatus(call))}${approvalFor(call)?.status === 'approved' ? '<span class="call-sub">전송 미연결 · 시안</span>' : ''}</td>${compact ? '' : `<td class="mono">${call.task}</td>`}<td class="row-arrow">${icon('arrow')}</td></tr>`).join('')}</tbody></table></div>`;
}

function metric(label, value, note, iconName, tone, page, filter = '') {
  return `<button type="button" class="metric-card ${tone}" data-page="${page}" ${filter ? `data-filter-status="${filter}"` : ''}><span class="metric-top"><span>${label}</span><span class="metric-icon ${tone}">${icon(iconName)}</span></span><span class="metric-value">${value}<small>${page === 'servers' ? '개' : '건'}</small></span><span class="metric-bottom"><span class="metric-note">${note}</span>${icon('arrow')}</span></button>`;
}

function renderOverview() {
  const queue = pendingApprovals();
  const denied = calls.filter(item => item.status === 'deny').length;
  const unknown = calls.filter(item => item.status === 'unknown').length;
  const active = servers.filter(item => item.status === 'active').length;
  const oldest = queue[0];
  const chart = [2, 3, 2, 4, 3, 7, 9, 5, 8, 6, 4, 5].map((height, index) => `<div class="chart-stack"><div class="chart-bar ${index >= 5 && index <= 8 ? 'strong' : ''}" style="height:${height * 10}%" title="예시 호출 흐름"></div></div>`).join('');
  return `${heading('Overview', 'MCP 운영 현황', '연결 상태와 지금 처리가 필요한 요청부터 확인해.', `<button class="button" type="button" data-page="logs">${icon('list')} 호출 로그 보기</button>`)}
    <div class="metrics">
      ${metric('연결된 MCP 서버', 0, `등록 ACTIVE ${active}개 · 실제 세션은 미연결`, 'server', 'blue', 'servers')}
      ${metric('승인 대기', queue.length, oldest ? `가장 오래된 요청 · ${waitingLabel(oldest)}` : '대기 중인 요청 없음', 'clock', 'orange', 'approvals')}
      ${metric('차단된 호출', denied, '실행 전에 차단된 요청 · 예시', 'shield', 'red', 'logs', 'deny')}
    </div>
    <section class="panel overview-servers" aria-label="연결된 MCP 서버">
      <div class="panel-heading"><div><h2>등록 ACTIVE 예시 <span class="heading-count">${active}</span></h2><p class="panel-subtitle">세션 미연결 · 서버 최대 수신 등급과 등록 예시</p></div><button class="button button-quiet" type="button" data-page="servers">서버 관리 ${icon('arrow')}</button></div>
      <div class="connected-grid">${servers.filter(server => server.status === 'active').map(server => `<button type="button" class="connected-server" data-action="server-detail" data-id="${escapeHTML(server.id)}"><span class="connected-server-top"><span class="server-avatar ${server.color}">${escapeHTML(server.short)}</span>${statusBadge(server.status)}</span><strong>${escapeHTML(server.name)}</strong><span class="connected-description">${escapeHTML(server.description)}</span>${serverSecurityBadge(server)}<span class="connected-meta"><span class="mono">${transportCode(server)}</span><br><span class="connected-endpoint mono">${escapeHTML(server.endpoint)}</span><span class="connected-boundary"><span>신뢰 경계</span>${escapeHTML(server.boundary)}</span></span></button>`).join('') || '<div class="empty-state">활성 서버가 없어.</div>'}</div>
    </section>
    <section class="attention-section" aria-label="지금 확인할 항목"><div class="section-heading"><div><h2>지금 확인할 항목</h2><p class="panel-subtitle">상태별로 필요한 다음 행동을 확인해</p></div><span class="attention-total">${queue.length + denied + unknown}건 확인 필요</span></div>
      <div class="attention-grid">
        <button class="attention-card amber" type="button" data-page="approvals"><span class="attention-top"><span class="risk-symbol amber">${icon('clock')}</span><span class="attention-count">${queue.length}<small>건</small></span></span><strong>승인 요청 검토</strong><span class="attention-description">${oldest ? `${waitingLabel(oldest)} · ${escapeHTML(oldest.server)}` : '모든 승인 요청을 처리했어.'}</span><span class="attention-hint">${queue.length ? '오래된 요청부터 하나씩 확인해.' : '새 요청이 들어오면 여기에 표시돼.'}</span><span class="attention-link">대기열 열기 ${icon('arrow')}</span></button>
        <button class="attention-card red" type="button" data-page="logs" data-filter-status="deny"><span class="attention-top"><span class="risk-symbol red">${icon('shield')}</span><span class="attention-count">${denied}<small>건</small></span></span><strong>차단 사유 확인</strong><span class="attention-description">서버 또는 목적지의 허용 등급 초과</span><span class="attention-hint">호출별 판정 근거와 경계를 확인해.</span><span class="attention-link">차단 로그 보기 ${icon('arrow')}</span></button>
        <button class="attention-card blue" type="button" data-page="logs" data-filter-status="unknown"><span class="attention-top"><span class="risk-symbol blue">${icon('info')}</span><span class="attention-count">${unknown}<small>건</small></span></span><strong>수신 결과 확인</strong><span class="attention-description">요청 전송 후 결과를 확인하지 못했어.</span><span class="attention-hint">수신처에서 확인이 필요해. 자동 재전송은 없어.</span><span class="attention-link">해당 호출 확인 ${icon('arrow')}</span></button>
      </div>
    </section>
    <section class="panel table-panel"><div class="panel-pad section-heading"><div><h2>최근 도구 호출</h2><p class="panel-subtitle">한국 시간 (KST, UTC+09:00) · 행을 클릭하면 상세 기록이 열려</p></div><button class="button button-quiet" type="button" data-page="logs">전체 보기 ${icon('arrow')}</button></div>${callTable(sortedCalls().slice(0, 5), true)}</section>
    <section class="panel panel-pad overview-flow" aria-label="도구 호출 흐름"><div class="panel-heading"><div><h2>도구 호출 흐름</h2><p class="panel-subtitle">24시간 시각화 예시 · 실제 집계 아님</p></div><span class="label-chip">참고 정보</span></div><div class="usage-chart" aria-label="예시 호출량 막대 그래프">${chart}</div><div class="chart-labels"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div><div class="chart-legend"><span><i class="legend-dot"></i>호출 집중 시간</span><span><i class="legend-dot muted"></i>그 외</span></div></section>`;
}

function openCallModal(id) {
  const call = calls.find(item => item.id === id);
  if (!call) return;
  state.selectedCall = id;
  state.modal = 'call';
  const server = servers.find(item => item.id === call.server);
  const approval = approvalFor(call);
  const execution = call.status === 'deny' ? '실행 전 차단' : call.status === 'ask' ? '전송 없음' : call.status === 'unknown' ? '전송 후 결과 불명' : '결과 공개 완료 · 예시';
  const events = [
    ['호출 요청 기록', formatDateTime(call.timestamp), `${call.task} · ${call.server} / ${call.tool}`],
    ['경계 평가 · ' + ({allow: 'ALLOW', ask: 'ASK', deny: 'DENY', unknown: '판정 미제공'})[call.status], '세부 시각 미제공', call.reason],
    [execution, '세부 시각 미제공', call.output]
  ];
  if (approval?.resolvedAt) events.push([`${statusLabels[approval.status]} · 시안`, formatDateTime(approval.resolvedAt), '브라우저에서 처리됨 · 실제 승인 소비 / 전송 없음']);
  const metadata = { demo: true, call_id: call.id, requested_at: call.timestamp, display_timezone: 'Asia/Seoul', task_id: call.task, mcp_server: call.server, tool: call.tool, kind: call.kind, data_label: call.label, initial_decision: call.status === 'unknown' ? null : call.status.toUpperCase(), current_status: callStatus(call), server_boundary: call.serverBoundary, destination_boundary: call.destinationBoundary, destination: call.destination, duration: call.duration, approval_id: approval?.id || null, approval_status: approval?.status || null };
  showModal(`${modalHeader('호출 상세 기록', `${call.id} · 합성 예시 데이터`)}<div class="modal-body call-detail-body">
    <div class="call-detail-hero"><div><span class="eyebrow">${escapeHTML(call.server)} · ${call.kind}</span><h3 class="mono">${escapeHTML(call.tool)}</h3><p>${formatDateTime(call.timestamp)}</p></div>${statusBadge(callStatus(call))}</div>
    <div class="reason-box"><strong>최초 판정 근거</strong>${escapeHTML(call.reason)}</div>
    <div class="call-detail-grid">
      <section class="detail-section"><h3>01 · 호출 정보</h3>${keyValues([['호출 ID', call.id], ['Task ID', call.task], ['요청 일시', formatDateTime(call.timestamp)], ['MCP 서버', escapeHTML(call.server)], ['접속 방식', escapeHTML(server?.transport || '미제공')], ['도구', escapeHTML(call.tool)], ['호출 종류', call.kind], ['소요 시간', call.duration === '—' ? '미측정 / 실행 미완료' : call.duration]])}</section>
      <section class="detail-section"><h3>02 · 경계와 실행 상태</h3>${keyValues([['데이터 등급', labelChip(call.label)], ['MCP 서버 경계', escapeHTML(call.serverBoundary)], ['목적지 경계', escapeHTML(call.destinationBoundary)], ['수신처', escapeHTML(call.destination)], ['최초 판정', call.status === 'unknown' ? '미제공 · 전송 후 결과 불명' : call.status.toUpperCase()], ['실행 상태', execution], ['승인 요청', approval ? `${approval.id} · ${statusLabels[approval.status]}` : '해당 없음'], ['재전송', call.status === 'unknown' ? '자동 재전송 금지 · 수신측 확인 필요' : '재전송 기록 미제공']])}</section>
      <section class="detail-section"><h3>03 · 입력 요약</h3><pre class="code-preview">${escapeHTML(call.input)}</pre></section>
      <section class="detail-section"><h3>04 · 출력 요약</h3><pre class="code-preview">${escapeHTML(call.output)}</pre></section>
    </div>
    <p class="detail-footnote">호출 로그에는 문서 원문·인증정보·전체 인자를 기록하지 않아. 승인 대상의 전체 인자는 승인 요청 화면에서 확인해.</p>
    <section class="detail-section"><h3>05 · 처리 기록</h3><p class="detail-footnote">합성 호출의 처리 순서야. 제공되지 않은 단계별 시각은 추정하지 않아.</p><ol class="event-timeline">${events.map(([title, time, description]) => `<li><div><strong>${escapeHTML(title)}</strong><span>${escapeHTML(time)}</span></div><p>${escapeHTML(description)}</p></li>`).join('')}</ol></section>
    <details class="metadata-details"><summary>06 · 구조화된 로그 메타데이터</summary><pre class="code-preview">${escapeHTML(JSON.stringify(metadata, null, 2))}</pre></details>
  </div><div class="modal-foot"><span class="detail-footnote">기록된 정보만 표시 · 미제공 항목은 별도 표시</span>${approval?.status === 'pending' ? `<button class="button" type="button" data-action="review-call-approval" data-id="${approval.id}">승인 요청 검토</button>` : ''}<button class="button button-primary" type="button" data-action="close-modal">닫기</button></div>`, 'modal-wide');
}

function renderLogs() {
  const items = filteredCalls();
  const serverOptions = servers.map(server => `<option value="${escapeHTML(server.id)}" ${state.serverFilter === server.id ? 'selected' : ''}>${escapeHTML(server.name)}</option>`).join('');
  return `${heading('Tool calls', 'MCP 도구 호출 로그', '도구 호출을 선택해 서버 경계, 목적지, 데이터 등급과 판정 근거를 확인해.', `<button class="button" type="button" data-action="export-csv">${icon('download')} CSV 내보내기</button>`)}
    <div class="filterbar"><label class="search-field">${icon('search')}<input id="call-search" type="search" placeholder="도구, MCP 서버, Task 검색" value="${escapeHTML(state.search)}" aria-label="호출 로그 검색"></label><select class="select" id="status-filter" aria-label="상태 필터"><option value="all">전체 상태</option>${['allow','ask','deny','unknown','approved','rejected'].map(value => `<option value="${value}" ${state.statusFilter === value ? 'selected' : ''}>${statusLabels[value]}</option>`).join('')}</select><select class="select" id="server-filter" aria-label="MCP 서버 필터"><option value="all">전체 MCP</option>${serverOptions}</select>${state.taskFilter ? `<button class="button" type="button" data-action="clear-task">Task ${escapeHTML(state.taskFilter)} ×</button>` : ''}<span class="filter-spacer"></span><button class="button" type="button" data-action="clear-filters">초기화</button></div>
    <div class="result-count">${items.length}개 호출 · 한국 시간 (KST) · 행을 클릭하면 상세 기록이 열려</div><section class="panel table-panel" aria-label="도구 호출 목록">${callTable(items)}</section>`;
}

function filteredApprovals() {
  const term = state.approvalSearch.trim().toLocaleLowerCase();
  return approvals.filter(item => (state.approvalView === 'pending' ? item.status === 'pending' : item.status !== 'pending') &&
    (state.approvalServer === 'all' || item.server === state.approvalServer) &&
    (!term || [item.id, item.callId, item.server, item.tool, item.destination].some(value => value.toLocaleLowerCase().includes(term))))
    .sort((a, b) => {
      const field = state.approvalView === 'history' ? 'resolvedAt' : 'timestamp';
      const difference = new Date(a[field] || a.timestamp) - new Date(b[field] || b.timestamp);
      return state.approvalSort === 'oldest' ? difference : -difference;
    });
}

function renderApprovals() {
  const queue = pendingApprovals();
  const items = filteredApprovals();
  const pageCount = Math.max(1, Math.ceil(items.length / APPROVAL_PAGE_SIZE));
  state.approvalPage = Math.min(state.approvalPage, pageCount);
  const start = (state.approvalPage - 1) * APPROVAL_PAGE_SIZE;
  const visible = items.slice(start, start + APPROVAL_PAGE_SIZE);
  const selected = visible.find(item => item.id === state.selectedApproval) || visible[0];
  state.selectedApproval = selected?.id || null;
  const longWaiting = queue.filter(item => waitingMinutes(item) >= 30).length;
  return `${heading('Approvals', '승인 대기', '오래 기다린 요청부터 검토하고, 처리한 요청은 이력에서 확인해.', `<button class="button" type="button" data-action="demo-backlog">대기 12건 추가 · 데모</button>`)}
    <div class="queue-summary"><div><span>검토할 요청</span><strong>${queue.length}<small>건</small></strong></div><div><span>30분 이상 대기</span><strong>${longWaiting}<small>건</small></strong></div><div><span>가장 오래된 요청</span><strong class="queue-oldest">${queue[0] ? waitingLabel(queue[0]) : '대기 없음'}</strong></div><p>대기 시간 기준<br>${formatDateTime(DEMO_NOW.toISOString())} · 고정 시안</p></div>
    <div class="approval-toolbar"><div class="segmented" aria-label="요청 상태"><button type="button" class="${state.approvalView === 'pending' ? 'active' : ''}" aria-pressed="${state.approvalView === 'pending'}" data-action="approval-view" data-id="pending">대기 중 <span>${queue.length}</span></button><button type="button" class="${state.approvalView === 'history' ? 'active' : ''}" aria-pressed="${state.approvalView === 'history'}" data-action="approval-view" data-id="history">처리 이력 <span>${approvals.length - queue.length}</span></button></div><span class="queue-guidance">한 건씩 검토 · 처리 후 다음 요청 표시</span></div>
    <div class="filterbar"><label class="search-field">${icon('search')}<input id="approval-search" type="search" placeholder="요청 ID, 도구, 수신처 검색" value="${escapeHTML(state.approvalSearch)}" aria-label="승인 요청 검색"></label><select class="select" id="approval-server" aria-label="승인 MCP 필터"><option value="all">전체 MCP</option>${[...new Set(approvals.map(item => item.server))].map(server => `<option value="${escapeHTML(server)}" ${state.approvalServer === server ? 'selected' : ''}>${escapeHTML(server)}</option>`).join('')}</select><select class="select" id="approval-sort" aria-label="승인 정렬"><option value="oldest" ${state.approvalSort === 'oldest' ? 'selected' : ''}>오래된 순</option><option value="newest" ${state.approvalSort === 'newest' ? 'selected' : ''}>최신순</option></select><button class="button" type="button" data-action="clear-approval-filters">필터 초기화</button></div>
    <div class="approval-layout"><section class="panel approval-list"><div class="panel-heading"><div><h2>${state.approvalView === 'pending' ? '검토 대기열' : '처리한 요청'}</h2><p class="panel-subtitle">${items.length}건${items.length ? ` 중 ${start + 1}–${start + visible.length}건 표시` : ''} · 페이지당 ${APPROVAL_PAGE_SIZE}건</p></div></div><div class="approval-items">${visible.map(item => `<button type="button" class="approval-item ${item.id === selected?.id ? 'selected' : ''}" aria-pressed="${item.id === selected?.id}" data-action="select-approval" data-id="${item.id}"><span class="server-avatar ${item.server === 'slack-notify' ? 'orange' : ''}">${item.server === 'slack-notify' ? 'SL' : 'GH'}</span><span class="approval-item-main"><span class="approval-item-top"><strong class="mono">${escapeHTML(item.tool)}</strong>${item.status === 'pending' ? `<span class="wait-chip ${waitingMinutes(item) >= 30 ? 'long-wait' : ''}">${waitingLabel(item)}</span>` : statusBadge(item.status)}</span><small>${escapeHTML(item.server)} · ${item.id}<br>${escapeHTML(item.destination)}</small><span class="approval-date">${formatDateTime(item.timestamp)}</span></span></button>`).join('') || `<div class="empty-state"><strong>${state.approvalSearch || state.approvalServer !== 'all' ? '조건에 맞는 요청이 없어' : state.approvalView === 'pending' ? '대기 중인 요청을 모두 처리했어' : '아직 처리한 요청이 없어'}</strong>${state.approvalSearch || state.approvalServer !== 'all' ? '검색어나 MCP 필터를 바꿔봐.' : state.approvalView === 'pending' ? '처리 이력에서 결과를 다시 확인할 수 있어.' : '승인하거나 거절한 요청은 여기에 모여.'}</div>`}</div><div class="pagination"><button class="button" type="button" data-action="approval-prev" ${state.approvalPage === 1 ? 'disabled' : ''}>이전</button><span>${state.approvalPage} / ${pageCount} 페이지</span><button class="button" type="button" data-action="approval-next" ${state.approvalPage === pageCount ? 'disabled' : ''}>다음</button></div></section>
    <section class="panel approval-detail" aria-label="승인 요청 상세">${selected ? `<div class="approval-detail-heading"><span class="eyebrow">Request snapshot · 예시</span>${statusBadge(selected.status)}</div><h2 class="mono">${escapeHTML(selected.tool)}</h2><p>수신처와 전송할 내용을 확인한 뒤 이 요청 한 건을 처리해.</p>${keyValues([['요청 / 호출 ID', `${selected.id} / ${selected.callId}`], ['요청 일시', formatDateTime(selected.timestamp)], ['대기 / 처리', selected.status === 'pending' ? waitingLabel(selected) : formatDateTime(selected.resolvedAt)], ['MCP 서버', escapeHTML(selected.server)], ['수신처 전체', escapeHTML(selected.destination)], ['데이터 등급', labelChip(selected.label)]])}<div class="detail-section approval-payload"><h3>전송 인자 전체 · 합성 예시</h3><pre class="approval-args">${escapeHTML(selected.args)}</pre></div>${selected.status === 'pending' ? `${selected.requestedDecision ? '<div class="reason-box"><strong>요청됨 · 적용 미확인</strong>백엔드 미연결로 승인 대기 상태를 유지해.</div>' : ''}<div class="approval-actions"><button class="button button-primary" type="button" data-action="request-approval" data-id="${selected.id}">이 요청 승인 · 시안</button><button class="button button-danger" type="button" data-action="request-rejection" data-id="${selected.id}">거절</button><button class="button button-quiet" type="button" data-action="select-call" data-id="${selected.callId}">호출 기록 ${icon('arrow')}</button></div>` : `<div class="reason-box"><strong>${statusLabels[selected.status]} · 시안 처리 완료</strong>실제 승인 소비나 도구 전송은 일어나지 않았어.</div>`}<p class="detail-footnote approval-disclaimer">이 화면은 브라우저 안의 합성 예시만 변경해. 실제 제품의 승인 상태는 백엔드 확정 이벤트로 갱신돼.</p>` : '<div class="empty-state"><strong>표시할 승인 요청이 없어</strong>다른 필터나 처리 이력을 확인해.</div>'}</section></div>`;
}

function addDemoBacklog() {
  const base = approvals.filter(item => !item.demoBacklog);
  const batch = approvals.filter(item => item.demoBacklog).length;
  for (let index = 0; index < 12; index++) {
    const sample = base[index % base.length];
    const source = calls.find(item => item.id === sample.callId);
    const sequence = batch + index + 1;
    const timestamp = new Date(DEMO_NOW.getTime() - (45 + sequence * 7) * 60000).toISOString();
    const callId = `call-queue-${String(sequence).padStart(3, '0')}`;
    const id = `ap-queue-${String(sequence).padStart(3, '0')}`;
    calls.push({ ...source, id: callId, task: `task-queue-${String(sequence).padStart(3, '0')}`, timestamp, demoBacklog: true });
    approvals.push({ ...sample, id, callId, timestamp, status: 'pending', resolvedAt: null, requestedDecision: null, demoBacklog: true });
  }
  state.approvalView = 'pending'; state.approvalSearch = ''; state.approvalServer = 'all'; state.approvalSort = 'oldest'; state.approvalPage = 1; state.selectedApproval = null;
  render(); toast('합성 요청 12건을 추가했어. 페이지와 오래된 순 정렬을 확인해.');
}

function filteredServers() {
  const term = state.mcpSearch.trim().toLocaleLowerCase();
  return servers.filter(server => (state.mcpTransport === 'all' || transportCode(server) === state.mcpTransport) &&
    (state.mcpStatus === 'all' || server.status === state.mcpStatus) &&
    (!term || [server.id, server.name, server.endpoint, server.boundary].some(value => value.toLocaleLowerCase().includes(term))));
}

function renderServers() {
  const items = filteredServers();
  return `${heading('Server registry', 'MCP 서버', '서버별 연결 설정, 신뢰 경계와 사용 가능 상태를 관리해.', `<button class="button button-primary" type="button" data-action="open-add">＋ MCP 서버 등록</button>`)}
    <div class="registry-summary"><span><i class="registry-dot active"></i>ACTIVE <strong>${servers.filter(s => s.status === 'active').length}</strong></span><span><i class="registry-dot pending"></i>PENDING <strong>${servers.filter(s => s.status === 'scan').length}</strong></span><span><i class="registry-dot blocked"></i>BLOCKED <strong>${servers.filter(s => s.status === 'blocked').length}</strong></span><p>등록 상태와 연결 상태는 별개야. 이 시안은 실제 세션·검사 결과를 수집하지 않아.</p></div>
    <div class="filterbar"><label class="search-field">${icon('search')}<input id="mcp-search" type="search" value="${escapeHTML(state.mcpSearch)}" placeholder="서버 ID, 이름, 엔드포인트 검색" aria-label="MCP 서버 검색"></label><select class="select" id="mcp-transport-filter" aria-label="MCP 전송 방식 필터"><option value="all">전체 transport</option><option value="stdio" ${state.mcpTransport === 'stdio' ? 'selected' : ''}>stdio</option><option value="streamable_http" ${state.mcpTransport === 'streamable_http' ? 'selected' : ''}>Streamable HTTP</option></select><select class="select" id="mcp-status-filter" aria-label="MCP 등록 상태 필터"><option value="all">전체 등록 상태</option>${[['active','ACTIVE'],['scan','PENDING'],['blocked','BLOCKED']].map(([value,label]) => `<option value="${value}" ${state.mcpStatus === value ? 'selected' : ''}>${label}</option>`).join('')}</select><button class="button" type="button" data-action="clear-mcp-filters">초기화</button></div>
    <section class="panel registry-panel" aria-label="MCP 서버 등록 목록"><div class="registry-table-heading"><h2>등록된 서버 <span class="heading-count">${items.length}</span></h2><span>등록 설정 · 합성 예시</span></div>${items.length ? `<div class="table-wrap"><table class="registry-table"><thead><tr><th>서버 식별자 <small>server_id</small></th><th>연결 설정 <small>transport / endpoint</small></th><th>신뢰 경계 <small>tier / max_label</small></th><th>등록 · 세션 <small>registry / connection</small></th><th>도구 정의 <small>tools</small></th><th><span class="sr-only">서버 상세</span></th></tr></thead><tbody>${items.map(server => `<tr data-action="server-detail" data-id="${escapeHTML(server.id)}" class="server-table-row"><td><button type="button" class="cell-button" data-action="server-detail" data-id="${escapeHTML(server.id)}" aria-label="${escapeHTML(server.id)} 서버 등록 상세"><strong class="call-name mono">${escapeHTML(server.id)}</strong><span class="call-sub">${escapeHTML(server.description || server.name)}</span></button></td><td><span class="transport-chip mono">${transportCode(server)}</span><span class="registry-endpoint mono">${escapeHTML(server.endpoint)}</span></td><td><span class="boundary-value mono">${escapeHTML(server.boundary)}</span><span class="server-limit">최대 ${labelChip(server.maxLabel)}</span></td><td>${registryBadge(server)}<span class="call-sub">세션: 미연결 · 시안</span></td><td><strong class="tools-count">${server.tools === null ? '미수집' : `${server.tools}개`}</strong><span class="call-sub">${server.tools === null ? 'tools/list 대기' : '등록 개수 · 예시'}</span></td><td class="row-arrow">${icon('arrow')}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty-state"><strong>조건에 맞는 서버가 없어</strong>검색어나 transport·등록 상태 필터를 바꿔봐.</div>'}</section>
    <div class="registry-reference"><section><h3>${icon('server')} 연결 방식 <code>transport</code></h3><p><strong>stdio</strong>는 실행 파일과 인자로 로컬 프로세스를 시작해 표준 입출력으로 통신해. <strong>Streamable HTTP</strong>는 지정한 MCP 엔드포인트 URL로 접속해.</p></section><section><h3>${icon('shield')} 신뢰 경계 <code>tier</code></h3><p>접속 방식이나 localhost 주소가 신뢰 등급을 결정하지 않아. 관리자가 지정한 <code>tier</code>와 <code>max_label</code>을 함께 평가해.</p></section></div>`;
}

function renderPolicy() {
  const matrix = [
    ['PUBLIC', '허용', '허용', '허용'],
    ['INTERNAL', '허용', '승인 대기', '승인 대기'],
    ['CONFIDENTIAL', '허용', '차단', '차단']
  ];
  return `${heading('Boundaries', '정책 · 경계', '데이터 등급과 MCP 서버·최종 목적지 경계의 기본 판정표를 확인해.')}
    <div class="notice-banner">${icon('info')} 아래는 설계 문서의 기본 정책 예시야. 실제 정책 편집이나 배포 기능은 연결돼 있지 않아.</div><div class="policy-grid"><section class="panel"><div class="panel-pad"><div class="panel-heading"><div><h2>기본 판정표</h2><p class="panel-subtitle">경계별 최대 허용 등급 검사를 통과한 뒤 적용</p></div></div></div><div class="table-wrap"><table class="policy-table"><thead><tr><th>데이터 등급</th><th>internal</th><th>approved_external</th><th>general_external</th></tr></thead><tbody>${matrix.map(row => `<tr><td>${labelChip(row[0])}</td>${row.slice(1).map(value => `<td>${statusBadge(value === '허용' ? 'allow' : value === '차단' ? 'deny' : 'ask')}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section><section class="panel panel-pad"><div class="panel-heading"><div><h2>평가 순서</h2><p class="panel-subtitle">표시용 설계 요약</p></div></div><div class="text-block"><ol class="rule-list"><li>현재 Task의 누적 등급을 확인해.</li><li>호출 인자를 먼저 받는 <strong>MCP 서버 경계</strong>를 검사해.</li><li>SEND라면 <strong>최종 목적지 경계</strong>도 검사해.</li><li>AGT 거절과 경계 판정 중 가장 엄격한 결과를 표시해.</li></ol></div></section></div>`;
}

function renderAudit() {
  return `${heading('Audit', '감사 기록', '원문 없이 호출 ID, 등급, 판정 코드 같은 메타데이터를 조회해.', `<button class="button" type="button" data-action="export-csv">${icon('download')} CSV 내보내기</button>`)}<div class="notice-banner">${icon('info')} 이 표는 합성 호출에서 만든 표시용 기록이야. 실제 감사 저장소와 연결되지 않아.</div><section class="panel table-panel"><div class="table-wrap"><table><thead><tr><th>시간</th><th>이벤트</th><th>호출 ID</th><th>Task</th><th>등급</th><th>결과</th></tr></thead><tbody>${calls.map(call => `<tr><td class="time-cell">${timeCell(call.timestamp)}</td><td class="mono">DECIDED</td><td class="mono">${call.id}</td><td class="mono">${call.task}</td><td>${labelChip(call.label)}</td><td>${statusBadge(call.status)}</td></tr>`).join('')}</tbody></table></div></section>`;
}

function renderRuns() {
  const tasks = [...new Set(calls.map(call => call.task))];
  return `${heading('Agent runs', '에이전트 실행', 'Task별 호출 흐름을 확인해. 실행 제어는 백엔드 계약 확정 후 연결돼.')}
    <div class="notice-banner">${icon('info')} 실제 모델 실행·중지 기능은 아직 없어. Task의 ‘호출 보기’ 버튼은 합성 로그를 필터링해.</div><section class="panel"><div class="panel-pad panel-heading"><div><h2>Task 목록</h2><p class="panel-subtitle">합성 예시</p></div></div>${tasks.map(task => { const count = calls.filter(call => call.task === task).length; return `<div class="run-card"><span class="server-avatar">T</span><span class="run-card-main"><strong class="mono">${task}</strong><small>도구 호출 ${count}건</small></span><button class="button" type="button" data-action="view-task" data-id="${task}">호출 보기 ${icon('arrow')}</button></div>`; }).join('')}</section>`;
}

function renderSettings() {
  return `${heading('Preferences', '설정', '이 시안에서 적용할 화면 표시 옵션을 선택해.')}
    <section class="panel" style="max-width:720px"><div class="setting-row"><div><strong>촘촘한 목록</strong><small>표 행 높이를 줄여 더 많은 호출을 보여줘.</small></div><button class="toggle ${state.compact ? 'on' : ''}" type="button" role="switch" aria-checked="${state.compact}" aria-label="촘촘한 목록" data-action="toggle-compact"></button></div><div class="setting-row"><div><strong>시안 상태 초기화</strong><small>추가한 MCP와 승인 시안 결과를 처음으로 되돌려.</small></div><button class="button" type="button" data-action="reset-demo">초기화</button></div></section><p class="detail-footnote" style="margin-top:14px">설정은 이 브라우저 탭에서만 유지돼. 새로고침하면 기본값으로 돌아가.</p>`;
}

function render() {
  const pages = { overview: renderOverview, logs: renderLogs, approvals: renderApprovals, servers: renderServers, policy: renderPolicy, audit: renderAudit, runs: renderRuns, settings: renderSettings };
  main.innerHTML = (pages[state.page] || renderOverview)();
  document.getElementById('breadcrumb-current').textContent = pageLabels[state.page];
  document.querySelectorAll('.nav-item[data-page]').forEach(button => {
    const active = button.dataset.page === state.page;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  });
  document.getElementById('approval-count').textContent = approvals.filter(item => item.status === 'pending').length;
  document.body.classList.toggle('compact', state.compact);
}

function setPage(page, filterStatus) {
  if (!pageLabels[page]) return;
  state.page = page;
  if (page === 'approvals' && state.approvalView !== 'pending') { state.approvalView = 'pending'; state.approvalPage = 1; state.selectedApproval = null; }
  if (filterStatus) { state.statusFilter = filterStatus; state.serverFilter = 'all'; state.search = ''; state.taskFilter = ''; }
  document.getElementById('sidebar').classList.remove('open');
  render();
  window.scrollTo({ top: 0, behavior: 'auto' });
}

function clearFilters() { state.search = ''; state.statusFilter = 'all'; state.serverFilter = 'all'; state.taskFilter = ''; render(); }
function toast(message) {
  const element = document.getElementById('toast');
  element.textContent = message;
  element.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove('show'), 3000);
}

function showModal(markup, variant = '') {
  if (!modalRoot.childElementCount) modalTrigger = document.activeElement;
  document.querySelector('.app-shell').inert = true;
  document.body.classList.add('modal-open');
  modalRoot.innerHTML = `<div class="modal-backdrop" data-action="backdrop"><div class="modal ${variant}" role="dialog" aria-modal="true" aria-labelledby="modal-title" tabindex="-1">${markup}</div></div>`;
  const initialFocus = modalRoot.querySelector('input:not([type="radio"]):not([disabled]), select:not([disabled]), textarea:not([disabled])') || modalRoot.querySelector('button');
  initialFocus?.focus();
}
function closeModal() {
  modalRoot.innerHTML = ''; state.modal = null;
  document.querySelector('.app-shell').inert = false;
  document.body.classList.remove('modal-open');
  if (modalTrigger?.isConnected) modalTrigger.focus();
  else main.focus();
}
function modalHeader(title, description) { return `<div class="modal-head"><div><h2 id="modal-title">${title}</h2><p>${description}</p></div><button class="icon-button" type="button" data-action="close-modal" aria-label="닫기">${icon('x')}</button></div>`; }

function openAddModal() {
  state.modal = 'add';
  showModal(`${modalHeader('MCP 서버 등록', '연결 설정과 서버 경계를 정의해. 등록 후 초기 상태는 PENDING이야.')}
    <form id="add-mcp-form" class="server-registration" novalidate>
      <div class="modal-body">
        <section class="form-section"><h3><span>01</span> 서버 식별</h3><div class="form-grid">
          <label class="field"><span class="field-label">서버 ID <code>server_id</code><b>필수</b></span><input name="server_id" maxlength="64" required placeholder="company-docs" autocomplete="off" spellcheck="false" aria-describedby="server-id-help"><span class="field-help" id="server-id-help">영문 소문자로 시작하는 2–64자. 소문자·숫자·하이픈(-)만 사용해. 도구 이름은 server_id__tool_name으로 구분돼.</span></label>
          <label class="field"><span class="field-label">표시 이름 <code>display_name</code></span><input name="display_name" maxlength="80" placeholder="예: 사내 문서 서버" autocomplete="off"><span class="field-help">목록에서 구분할 이름. 비워 두면 서버 ID를 사용해.</span></label>
        </div></section>
        <section class="form-section"><h3><span>02</span> 연결 설정 <code>transport</code></h3>
          <div class="transport-options"><label class="transport-option"><input type="radio" name="transport" value="stdio" checked><span><strong>stdio <small>로컬 프로세스</small></strong><em>실행 파일 · 인자 배열 · 환경변수</em></span></label><label class="transport-option"><input type="radio" name="transport" value="Streamable HTTP"><span><strong>Streamable HTTP <small>HTTP 엔드포인트</small></strong><em>MCP 서버 URL</em></span></label></div>
          <fieldset id="stdio-fields" class="transport-fields"><legend class="sr-only">stdio 설정</legend>
            <label class="field"><span class="field-label">실행 파일 절대 경로 <code>command</code><b>필수</b></span><input name="command" maxlength="1024" required placeholder="C:/mcp/docs/.venv/Scripts/python.exe" autocomplete="off" spellcheck="false"><span class="field-help">실행 파일 경로만 입력해. -m 같은 인자는 아래 배열에 나눠 입력해.</span></label>
            <div class="form-grid"><label class="field"><span class="field-label">인자 배열 <code>args</code></span><textarea name="args" rows="3" maxlength="8192" spellcheck="false" placeholder='["-m", "company_docs.server"]'>[]</textarea><span class="field-help">JSON 문자열 배열. 각 항목이 인자 하나로 전달돼. 인자가 없으면 []를 사용해.</span></label>
            <label class="field"><span class="field-label">전달할 환경변수 <code>env_allow</code></span><textarea name="env_allow" rows="3" maxlength="2048" spellcheck="false" placeholder="LANG&#10;TZ"></textarea><span class="field-help">환경변수 이름만 한 줄에 하나씩 입력해. 값이나 KEY=VALUE 형식은 입력하지 않아.</span></label></div>
          </fieldset>
          <fieldset id="http-fields" class="transport-fields" hidden disabled><legend class="sr-only">Streamable HTTP 설정</legend><label class="field"><span class="field-label">MCP 엔드포인트 URL <code>url</code><b>필수</b></span><input name="url" type="url" maxlength="2048" required placeholder="https://mcp.example.com/mcp" autocomplete="off" spellcheck="false"><span class="field-help">MCP 요청을 받는 전체 http(s) URL. 웹사이트 주소와 구분해 서버가 제공한 경로까지 입력해.</span></label><p class="transport-note">예: https://mcp.example.com/mcp · 로컬 개발 예: http://127.0.0.1:8765/mcp<br>인증·세션 협상은 실제 연결 단계에서 확인해. 이 시안은 자격 증명을 받지 않아.</p></fieldset>
        </section>
        <section class="form-section"><h3><span>03</span> 서버 경계</h3><div class="form-grid">
          <label class="field"><span class="field-label">신뢰 경계 <code>tier</code></span><select name="boundary" id="registration-boundary">${[['general_external','일반 외부'],['approved_external','승인된 외부'],['internal','내부']].map(([value,label]) => `<option value="${value}">${value} · ${label}</option>`).join('')}</select><span class="field-help">접속 방식과 별도로 지정해. stdio나 localhost도 외부 경계로 등록할 수 있어.</span></label>
          <label class="field"><span class="field-label">최대 수신 등급 <code>max_label</code></span><select name="max_label" id="registration-max-label"><option value="PUBLIC">PUBLIC · 공개</option><option value="INTERNAL">INTERNAL · 내부</option><option value="CONFIDENTIAL" disabled>CONFIDENTIAL · 기밀 (내부 경계만)</option></select><span class="field-help" id="registration-label-help" role="status">서버가 받을 수 있는 최대 등급. 외부 경계는 INTERNAL 이하로 제한돼.</span></label>
        </div></section>
        <details class="registration-preview"><summary>등록 설정 미리보기 <code>JSON · 시안</code></summary><pre class="code-preview" id="registration-preview"></pre></details>
        <p class="form-error" id="add-error" role="alert" tabindex="-1"></p>
      </div>
      <div class="modal-foot"><span class="registration-footnote"><span class="status scan">PENDING</span> 등록 → 도구 검사 → 관리자 확인 → ACTIVE</span><button class="button" type="button" data-action="close-modal">취소</button><button class="button button-primary" type="submit">검토 대기로 등록</button></div>
    </form>`, 'modal-registration');
  updateRegistrationForm();
}

// UI-only format validation. Execution, URL trust and registry enforcement belong to the backend.
function parseServerDraft(data) {
  const get = key => String(data.get(key) || '').trim();
  const id = get('server_id'), name = get('display_name') || id;
  const transport = get('transport'), boundary = get('boundary'), maxLabel = get('max_label');
  const invalid = (field, error) => ({ field, error });
  if (!/^[a-z][a-z0-9-]{1,63}$/.test(id)) return invalid('server_id', '서버 ID는 소문자로 시작하는 2–64자의 소문자·숫자·하이픈으로 입력해.');
  if (servers.some(server => server.id.toLowerCase() === id.toLowerCase())) return invalid('server_id', '이미 등록된 서버 ID야. 다른 ID를 입력해.');
  if (name.length > 80) return invalid('display_name', '표시 이름은 80자 이내로 입력해.');
  if (!['stdio', 'Streamable HTTP'].includes(transport)) return invalid('transport', '지원하는 전송 방식을 선택해.');
  if (!Object.hasOwn(boundaryDescriptions, boundary)) return invalid('boundary', '신뢰 경계를 선택해.');
  if (!['PUBLIC', 'INTERNAL', 'CONFIDENTIAL'].includes(maxLabel)) return invalid('max_label', '최대 수신 등급을 선택해.');
  if (boundary !== 'internal' && maxLabel === 'CONFIDENTIAL') return invalid('max_label', '외부 경계의 최대 수신 등급은 PUBLIC 또는 INTERNAL이어야 해.');
  const server = { id, name, transport, boundary, maxLabel, short: id.slice(0, 2).toUpperCase(), color: '', tools: null, status: 'scan', description: name, demoAdded: true };
  if (transport === 'stdio') {
    const command = get('command');
    const slashes = command.replaceAll(String.fromCharCode(92), '/');
    const isAbsolute = command.startsWith('/') || slashes.startsWith('//') || (/^[A-Za-z]$/.test(slashes[0] || '') && slashes.slice(1, 3) === ':/');
    if (!command || command.length > 1024 || !isAbsolute || [0, 10, 13].some(code => command.includes(String.fromCharCode(code)))) return invalid('command', '실행 파일의 절대 경로를 입력해. 예: C:/mcp/python.exe 또는 /opt/mcp/bin/python');
    let args;
    try { args = JSON.parse(get('args') || '[]'); }
    catch (error) { if (!(error instanceof SyntaxError)) throw error; return invalid('args', '인자는 JSON 문자열 배열이어야 해. 예: ["-m", "company_docs.server"]'); }
    if (!Array.isArray(args) || args.some(value => typeof value !== 'string') || get('args').length > 8192) return invalid('args', '인자는 문자열만 포함하는 JSON 배열로 입력해.');
    const envAllow = [...new Set(get('env_allow').split(/\r?\n/).map(value => value.trim()).filter(Boolean))];
    if (get('env_allow').length > 2048 || envAllow.some(value => !/^[A-Za-z_][A-Za-z0-9_]*$/.test(value))) return invalid('env_allow', '환경변수 이름을 한 줄에 하나씩 입력해. 값이나 등호(=)는 포함할 수 없어.');
    Object.assign(server, { endpoint: command, command, args, envAllow });
  } else {
    const endpoint = get('url');
    let url;
    try { url = new URL(endpoint); }
    catch (error) { if (!(error instanceof TypeError)) throw error; return invalid('url', '유효한 MCP 엔드포인트 URL을 입력해. 예: https://mcp.example.com/mcp'); }
    if (!/^https?:\/\//i.test(endpoint) || !['http:', 'https:'].includes(url.protocol) || endpoint.length > 2048 || /[\s\u0000]/.test(endpoint)) return invalid('url', '공백 없는 http:// 또는 https:// 전체 URL을 입력해.');
    if (url.username || url.password || url.hash) return invalid('url', 'URL에 사용자명·비밀번호나 #fragment를 포함할 수 없어. MCP 엔드포인트만 입력해.');
    server.endpoint = endpoint;
  }
  return { server };
}

function updateRegistrationForm() {
  const form = document.getElementById('add-mcp-form');
  if (!form) return;
  const isStdio = form.elements.namedItem('transport').value === 'stdio';
  for (const [id, enabled] of [['stdio-fields', isStdio], ['http-fields', !isStdio]]) {
    const fieldset = document.getElementById(id);
    fieldset.hidden = !enabled; fieldset.disabled = !enabled;
  }
  const boundary = form.elements.namedItem('boundary').value;
  const maxLabel = form.elements.namedItem('max_label');
  maxLabel.querySelector('option[value="CONFIDENTIAL"]').disabled = boundary !== 'internal';
  const downgraded = boundary !== 'internal' && maxLabel.value === 'CONFIDENTIAL';
  if (downgraded) maxLabel.value = 'INTERNAL';
  document.getElementById('registration-label-help').textContent = downgraded ? '외부 경계로 변경되어 최대 수신 등급을 INTERNAL로 조정했어.' : boundary === 'internal' ? '이 서버가 받을 수 있는 최대 데이터 등급을 지정해.' : '서버가 받을 수 있는 최대 등급. 외부 경계는 INTERNAL 이하로 제한돼.';
  const draft = parseServerDraft(new FormData(form));
  document.getElementById('registration-preview').textContent = draft.error ? draft.error : JSON.stringify(serverConfig(draft.server), null, 2);
}

function openServerModal(id) {
  const server = servers.find(item => item.id === id);
  if (!server) return;
  state.modal = 'server';
  const observedTools = [...new Map(calls.filter(call => call.server === server.id).map(call => [call.tool, call])).values()];
  const connectionFields = server.transport === 'stdio'
    ? [['전송 방식', 'stdio · 표준 입출력'], ['실행 파일 · command', escapeHTML(server.command)], ['인자 · args', `<code>${escapeHTML(JSON.stringify(server.args))}</code>`], ['환경변수 · env_allow', escapeHTML(server.envAllow.join(', ') || '없음')]]
    : [['전송 방식', 'Streamable HTTP'], ['엔드포인트 · url', escapeHTML(server.endpoint)], ['인증 구성', '미설정 · 시안'], ['HTTP 세션', '미연결 · 시안']];
  showModal(`${modalHeader('MCP 서버 등록 상세', `${escapeHTML(server.id)} · 합성 등록 정보`)}<div class="modal-body server-detail-body">
    <div class="server-detail-hero"><div><span class="eyebrow">Server registration</span><h3>${escapeHTML(server.name)}</h3><code>${escapeHTML(server.id)}</code></div>${registryBadge(server)}</div>
    <div class="server-detail-grid"><section class="detail-section"><h3>01 · 연결 설정</h3>${keyValues(connectionFields)}</section><section class="detail-section"><h3>02 · 신뢰 경계</h3>${keyValues([['서버 경계 · tier', escapeHTML(server.boundary)], ['구분', escapeHTML(boundaryDescriptions[server.boundary])], ['최대 등급 · max_label', labelChip(server.maxLabel)], ['평가 범위', 'READ · SEND 모두 서버 경계 평가'], ['SEND 추가 평가', '최종 목적지 경계 별도 평가']])}</section></div>
    <section class="detail-section"><h3>03 · 등록 및 검사 상태</h3><div class="inspection-grid"><div><span>Registry</span>${registryBadge(server)}<small>${server.status === 'active' ? '활성 등록 예시 · 실제 연결을 뜻하지 않음' : '신규 등록 · 검사와 관리자 확인 필요'}</small></div><div><span>Connection</span><strong>미연결</strong><small>세션 / protocolVersion 미수집</small></div><div><span>Scanner</span><strong>미실행</strong><small>분석기 / 검사 일시 미수집</small></div><div><span>Tool fingerprint</span><strong>미수집</strong><small>도구 정의별 SHA-256</small></div></div><p class="detail-footnote">검사 완료와 관리자 확인 후 ACTIVE로 전환돼. 도구 지문이 변경되면 PENDING으로 돌아가고 기존 승인은 무효화돼.</p></section>
    <section class="detail-section"><div class="section-heading"><h3>04 · 로그에서 확인한 도구</h3><span class="detail-footnote">${observedTools.length}개 관측 / 등록 ${server.tools === null ? '미수집' : server.tools + '개 · 예시'}</span></div>${observedTools.length ? `<div class="table-wrap"><table class="server-tools-table"><thead><tr><th>도구 이름</th><th>종류</th><th>모델에 노출할 이름</th><th>정의 스키마</th></tr></thead><tbody>${observedTools.map(call => `<tr><td class="mono">${escapeHTML(call.tool)}</td><td>${call.kind}</td><td class="mono">${escapeHTML(server.id)}__${escapeHTML(call.tool)}</td><td>미수집</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty-state"><strong>수집한 도구 정의가 없어</strong>실제 연결 후 tools/list와 정의 검사가 필요해.</div>'}</section>
    <details class="metadata-details"><summary>05 · 등록 설정 <code>JSON · 시안</code></summary><pre class="code-preview">${escapeHTML(JSON.stringify(serverConfig(server), null, 2))}</pre></details>
  </div><div class="modal-foot"><span class="detail-footnote">연결·스캔·관리자 활성화는 실행되지 않아.</span><button class="button" type="button" data-action="server-calls" data-id="${escapeHTML(server.id)}">이 서버 호출 로그</button><button class="button button-primary" type="button" data-action="close-modal">닫기</button></div>`, 'modal-wide');
}

function openApprovalConfirm(id, decision) {
  const item = approvals.find(entry => entry.id === id && entry.status === 'pending');
  if (!item) { toast('이미 처리된 예시 요청이야.'); return; }
  state.modal = { type: 'approval', id, decision };
  const title = decision === 'approved' ? '승인 시안 확인' : '거절 시안 확인';
  showModal(`${modalHeader(title, '브라우저 안의 합성 상태만 변경돼.')}<div class="modal-body"><div class="confirm-summary"><strong>${escapeHTML(item.tool)}</strong><br>MCP: ${escapeHTML(item.server)}<br>수신처: ${escapeHTML(item.destination)}<br>데이터 등급: ${escapeHTML(item.label)}</div><p class="warning-note">이 버튼은 실제 요청을 전송하거나 승인하지 않아.</p></div><div class="modal-foot"><button class="button" type="button" data-action="close-modal">취소</button><button class="button ${decision === 'approved' ? 'button-primary' : 'button-danger'}" type="button" data-action="confirm-approval">${decision === 'approved' ? '승인 요청 시안' : '거절 요청 시안'}</button></div>`);
}

function exportCSV() {
  const exportedCalls = state.page === 'audit' ? calls : filteredCalls();
  const rows = [['timestamp', 'call_id', 'task_id', 'mcp_server', 'tool', 'kind', 'label', 'initial_decision', 'current_status', 'approval_status'], ...exportedCalls.map(call => [call.timestamp, call.id, call.task, call.server, call.tool, call.kind, call.label, call.status === 'unknown' ? '' : call.status, callStatus(call), approvalFor(call)?.status || ''])];
  const csv = rows.map(row => row.map(value => `"${String(value).replace(/^([=+@-])/, "'$1").replaceAll('"', '""')}"`).join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob(['\uFEFF', csv], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url; link.download = 'mcp-guard-demo-calls.csv'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('합성 호출 메타데이터를 CSV로 내보냈어.');
}

document.addEventListener('click', event => {
  const pageButton = event.target.closest('[data-page]');
  if (pageButton) { setPage(pageButton.dataset.page, pageButton.dataset.filterStatus); return; }
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const { action, id } = button.dataset;
  if (action === 'backdrop' && event.target !== button) return;
  switch (action) {
    case 'open-add': openAddModal(); break;
    case 'close-modal': case 'backdrop': closeModal(); break;
    case 'toggle-menu': document.getElementById('sidebar').classList.toggle('open'); break;
    case 'select-call': openCallModal(id); break;
    case 'review-call-approval': {
      closeModal(); state.approvalView = 'pending'; state.approvalSearch = ''; state.approvalServer = 'all'; state.approvalSort = 'oldest';
      const index = filteredApprovals().findIndex(item => item.id === id);
      state.approvalPage = Math.floor(Math.max(0, index) / APPROVAL_PAGE_SIZE) + 1;
      state.selectedApproval = id; setPage('approvals'); break;
    }
    case 'approval-view': state.approvalView = id; state.approvalPage = 1; state.selectedApproval = null; render(); break;
    case 'approval-prev': state.approvalPage = Math.max(1, state.approvalPage - 1); state.selectedApproval = null; render(); break;
    case 'approval-next': state.approvalPage++; state.selectedApproval = null; render(); break;
    case 'clear-approval-filters': state.approvalSearch = ''; state.approvalServer = 'all'; state.approvalSort = 'oldest'; state.approvalPage = 1; state.selectedApproval = null; render(); break;
    case 'demo-backlog': addDemoBacklog(); break;
    case 'select-approval': state.selectedApproval = id; render(); main.querySelector('.approval-item.selected')?.focus(); break;
    case 'request-approval': openApprovalConfirm(id, 'approved'); break;
    case 'request-rejection': openApprovalConfirm(id, 'rejected'); break;
    case 'confirm-approval': {
      const current = state.modal;
      if (!current || current.type !== 'approval') break;
      const item = approvals.find(entry => entry.id === current.id);
      if (item && item.status === 'pending') {
        button.disabled = true;
        item.requestedDecision = current.decision;
        toast('요청됨 · 백엔드 미연결로 적용 여부는 미확인. 승인 상태는 대기 중이야.');
      }
      closeModal(); render();
      (main.querySelector('.approval-detail [data-action="request-approval"]') || main.querySelector('[data-action="approval-view"]'))?.focus(); break;
    }
    case 'server-detail': openServerModal(id); break;
    case 'server-calls': closeModal(); state.serverFilter = id; state.statusFilter = 'all'; state.taskFilter = ''; state.search = ''; setPage('logs'); break;
    case 'clear-mcp-filters': state.mcpSearch = ''; state.mcpTransport = 'all'; state.mcpStatus = 'all'; render(); break;
    case 'clear-filters': clearFilters(); break;
    case 'clear-task': state.taskFilter = ''; render(); break;
    case 'view-task': state.taskFilter = id; state.statusFilter = 'all'; state.serverFilter = 'all'; state.search = ''; setPage('logs'); break;
    case 'export-csv': exportCSV(); break;
    case 'toggle-compact': state.compact = !state.compact; render(); break;
    case 'reset-demo': { for (let i = approvals.length - 1; i >= 0; i--) if (approvals[i].demoBacklog) approvals.splice(i, 1); for (let i = calls.length - 1; i >= 0; i--) if (calls[i].demoBacklog) calls.splice(i, 1); approvals.forEach(item => { item.status = 'pending'; delete item.resolvedAt; }); state.approvalView = 'pending'; state.approvalSearch = ''; state.approvalServer = 'all'; state.approvalSort = 'oldest'; state.approvalPage = 1; state.selectedApproval = null; servers.splice(4); state.mcpSearch = ''; state.mcpTransport = 'all'; state.mcpStatus = 'all'; state.compact = false; state.search = ''; state.statusFilter = 'all'; state.serverFilter = 'all'; state.taskFilter = ''; render(); toast('시안 상태를 초기화했어.'); break; }
    default: break;
  }
});

document.addEventListener('input', event => {
  if (event.isComposing) return;
  if (event.target.closest('#add-mcp-form')) {
    if (event.target.type === 'radio' || event.target.tagName === 'SELECT') return;
    event.target.removeAttribute('aria-invalid');
    document.getElementById('add-error').textContent = '';
    updateRegistrationForm(); return;
  }
  const id = event.target.id;
  if (!['call-search', 'approval-search', 'mcp-search'].includes(id)) return;
  if (id === 'call-search') state.search = event.target.value;
  else if (id === 'mcp-search') state.mcpSearch = event.target.value;
  else { state.approvalSearch = event.target.value; state.approvalPage = 1; state.selectedApproval = null; }
  const position = event.target.selectionStart;
  render();
  const input = document.getElementById(id);
  input?.focus();
  if (position !== null && input?.setSelectionRange) input.setSelectionRange(position, position);
});

document.addEventListener('change', event => {
  if (event.target.closest('#add-mcp-form')) {
    document.getElementById('add-error').textContent = '';
    updateRegistrationForm(); return;
  }
  const fields = { 'status-filter': 'statusFilter', 'server-filter': 'serverFilter', 'approval-server': 'approvalServer', 'approval-sort': 'approvalSort', 'mcp-transport-filter': 'mcpTransport', 'mcp-status-filter': 'mcpStatus' };
  const field = fields[event.target.id];
  if (!field) return;
  const id = event.target.id;
  state[field] = event.target.value;
  if (id.startsWith('approval-')) { state.approvalPage = 1; state.selectedApproval = null; }
  render(); document.getElementById(id)?.focus();
});

document.addEventListener('submit', event => {
  if (event.target.id !== 'add-mcp-form') return;
  event.preventDefault();
  const draft = parseServerDraft(new FormData(event.target));
  const error = document.getElementById('add-error');
  if (draft.error) {
    error.textContent = draft.error;
    const field = event.target.elements.namedItem(draft.field);
    if (field?.setAttribute) { field.setAttribute('aria-invalid', 'true'); field.setAttribute('aria-describedby', draft.field === 'server_id' ? 'server-id-help add-error' : 'add-error'); }
    if (field?.focus) field.focus(); else error.focus();
    return;
  }
  servers.push(draft.server);
  state.mcpSearch = ''; state.mcpTransport = 'all'; state.mcpStatus = 'all';
  closeModal(); setPage('servers'); toast(`${draft.server.id}를 PENDING으로 등록했어. 실제 실행·접속은 없어.`);
});

document.addEventListener('keydown', event => {
  if (event.key === 'Tab' && state.modal) {
    const focusable = [...modalRoot.querySelectorAll('button, input, select, textarea, summary, [tabindex="0"]')].filter(element => !element.matches(':disabled') && !element.closest('[hidden]'));
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }
  if (event.key === 'Escape') { if (state.modal) closeModal(); else document.getElementById('sidebar').classList.remove('open'); }
});

render();
