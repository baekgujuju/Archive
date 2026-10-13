"""메모 기능 공용 모듈 - PC/모바일/IB 공용"""

MEMO_CSS = """
/* 포스트잇 메모 */
.memo-btn{border:none;background:none;cursor:pointer;font-size:16px;padding:4px;opacity:.4}
.memo-btn:hover{opacity:1}
.memo-btn.has-memo{opacity:1}
.memo-text-btn{display:inline-block;margin:6px 0;padding:6px 12px;background:#fff9c4;border:1px solid #e6d76c;border-radius:6px;font-size:12px;opacity:1;color:#666}
.memo-text-btn:hover{background:#fff3a0}
.memo-text-btn.has-memo{background:#ffeb3b;border-color:#f9a825;color:#333;font-weight:600}
.memo-icon-btn{border:none;background:none;cursor:pointer;padding:2px;opacity:.4}
.memo-icon-btn svg{width:18px;height:18px;fill:none;stroke:#888;stroke-width:2}
.memo-icon-btn.has-memo{opacity:1}
.memo-icon-btn.has-memo svg{fill:#ffeb3b;stroke:#f9a825}
.memo-icon-btn.memo-hidden{display:none}
.memo-icon-btn:hover{transform:scale(1.2)}
.memo-modal{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.4);z-index:99999;display:flex;align-items:center;justify-content:center}
.memo-modal[hidden]{display:none}
/* 열린 <dialog> 안에서 호출될 때: dialog 안으로 이동되므로 absolute로 dialog 전체를 덮음 */
dialog .memo-modal{position:absolute;inset:0}
.memo-postit{width:min(400px,90vw);min-height:300px;background:#fff9c4;padding:20px;box-shadow:0 4px 20px rgba(0,0,0,.3);transform:rotate(-1deg);position:relative}
.memo-postit::before{content:'';position:absolute;top:-10px;left:50%;transform:translateX(-50%);width:80px;height:24px;background:rgba(255,255,255,.6);border:1px solid #ddd}
.memo-postit h3{margin:0 0 12px;font-size:14px;color:#666}
.memo-postit textarea{width:100%;min-height:180px;border:none;background:transparent;font-size:16px;line-height:1.6;resize:vertical;outline:none;font-family:inherit}
.memo-postit .memo-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:12px}
.memo-postit button{padding:8px 16px;border:none;border-radius:6px;cursor:pointer;font-size:13px}
.memo-save{background:#1a73e8;color:#fff}
.memo-cancel{background:#ddd}
.memo-delete{background:#e74c3c;color:#fff;margin-right:auto}
"""

MEMO_JS = """
// 메모 기능
const MEMO_PREFIX = 'archive_memo_';
function getMemoKey(id) { return MEMO_PREFIX + id; }
function getMemo(id) { return localStorage.getItem(getMemoKey(id)) || ''; }
function setMemo(id, text) {
  if (text.trim()) localStorage.setItem(getMemoKey(id), text);
  else localStorage.removeItem(getMemoKey(id));
  updateMemoBtn(id);
}
function updateMemoBtn(id) {
  if (!id) return;
  const hasMemo = !!localStorage.getItem(MEMO_PREFIX + id);
  // ID 이스케이프 없이 안전하게 모든 버튼 순회
  document.querySelectorAll('.memo-btn').forEach(btn => {
    if (btn.dataset.memoId !== id) return;
    btn.classList.toggle('has-memo', hasMemo);
    if (btn.classList.contains('memo-text-btn')) {
      btn.textContent = hasMemo ? '📝 메모있음' : '📝 메모';
    } else if (btn.classList.contains('memo-icon-btn')) {
      btn.classList.toggle('memo-hidden', !hasMemo);
    }
  });
}
function openMemo(id, title, btn) {
  const modal = document.getElementById('memo-modal');
  const textarea = document.getElementById('memo-textarea');
  const titleEl = document.getElementById('memo-title');
  // 이전에 dialog 안으로 옮겨진 잔재가 있으면 원위치로 복귀 (멱등성 보장)
  if (modal._originParent) {
    modal._originParent.insertBefore(modal, modal._originNext);
    modal._originParent = null;
    modal._originNext = null;
  }
  textarea.value = getMemo(id);
  titleEl.textContent = title || '메모';
  modal.dataset.memoId = id;
  // 열린 <dialog> 안에서 호출된 경우 top layer 충돌 회피를 위해 dialog 안으로 이동
  const dlg = (btn && btn.closest) ? btn.closest('dialog[open]') : null;
  if (dlg) {
    modal._originParent = modal.parentNode;
    modal._originNext = modal.nextSibling;
    dlg.appendChild(modal);
  }
  modal.hidden = false;
  textarea.focus();
}
function closeMemo() {
  const modal = document.getElementById('memo-modal');
  if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  modal.hidden = true;
  if (modal._originParent) {
    modal._originParent.insertBefore(modal, modal._originNext);
    modal._originParent = null;
    modal._originNext = null;
  }
}
function initMemo() {
  document.querySelectorAll('.memo-btn').forEach(btn => {
    const id = btn.dataset.memoId;
    if (!id) return;
    const hasMemo = !!localStorage.getItem(MEMO_PREFIX + id);
    btn.classList.toggle('has-memo', hasMemo);
    if (btn.classList.contains('memo-text-btn')) {
      btn.innerHTML = hasMemo ? '📝 메모있음' : '📝 메모';
    } else if (btn.classList.contains('memo-icon-btn')) {
      btn.classList.toggle('memo-hidden', !hasMemo);
    }
  });
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initMemo);
} else {
  initMemo();
}
document.addEventListener('DOMContentLoaded', function() {
  // 메모 버튼 클릭
  document.addEventListener('click', function(e) {
    const btn = e.target.closest('.memo-btn');
    if (btn) {
      e.stopPropagation();
      e.preventDefault();
      openMemo(btn.dataset.memoId, btn.dataset.memoTitle, btn);
      return;
    }
    if (e.target.id === 'memo-save') {
      const modal = document.getElementById('memo-modal');
      setMemo(modal.dataset.memoId, document.getElementById('memo-textarea').value);
      closeMemo();
    } else if (e.target.id === 'memo-cancel') {
      closeMemo();
    } else if (e.target.id === 'memo-delete') {
      const modal = document.getElementById('memo-modal');
      setMemo(modal.dataset.memoId, '');
      closeMemo();
    }
  });
  // ESC로 닫기
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeMemo();
  });
});
"""

MEMO_MODAL_HTML = """
<div class="memo-modal" id="memo-modal" hidden>
  <div class="memo-postit">
    <h3 id="memo-title">메모</h3>
    <textarea id="memo-textarea" placeholder="메모를 입력하세요..."></textarea>
    <div class="memo-actions">
      <button id="memo-delete">삭제</button>
      <button id="memo-cancel">취소</button>
      <button id="memo-save">저장</button>
    </div>
  </div>
</div>
"""

def memo_button(memo_id, title=""):
    """메모 버튼 HTML 생성 - 북마크 옆 아이콘 (메모 있을 때만 표시)"""
    from html import escape
    safe_title = escape(title[:50], quote=True)
    safe_id = escape(memo_id)
    return f'<button type="button" class="memo-btn memo-icon-btn memo-hidden" data-memo-id="{safe_id}" data-memo-title="{safe_title}" title="메모"><svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6z"/><path d="M14 2v6h6"/><path d="M9 13h6M9 17h4"/></svg></button>'

def memo_button_full(memo_id, title=""):
    """메모 버튼 HTML 생성 - 읽기 모달용 전체 버튼"""
    from html import escape
    safe_title = escape(title[:50], quote=True)
    safe_id = escape(memo_id)
    return f'<button type="button" class="memo-btn memo-text-btn" data-memo-id="{safe_id}" data-memo-title="{safe_title}">📝 메모</button>'
