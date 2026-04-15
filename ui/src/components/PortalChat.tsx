import React, { useEffect, useRef, useState } from 'react';
import styles from './PortalChat.module.css';
import { Portal } from './PortalList';
import { api } from '../api/client';

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

/* ------------------------------------------------------------------ */
/*  Types                                                               */
/* ------------------------------------------------------------------ */

interface PortalChatProps {
  portal: Portal;
  onBack: () => void;
  hideBackButton?: boolean;
}

type MsgRole = 'user' | 'assistant' | 'event';

interface ChatMsg {
  id: string;
  role: MsgRole;
  content: string;
  eventType?: string;
  meta?: any;
}

type ChannelName = 'telegram' | 'dingtalk' | 'weixin' | 'feishu';

/* ------------------------------------------------------------------ */
/*  SSE helper                                                          */
/* ------------------------------------------------------------------ */

function portalChat(
  portalId: string,
  sessionId: string,
  message: string,
  userId: string,
  onEvent: (e: any) => void,
  onDone: () => void,
  onError: (e: Error) => void
): () => void {
  const ctrl = new AbortController();
  (async () => {
    try {
      const res = await fetch(`${BASE_URL}/api/portals/${portalId}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, message, user_id: userId, stream: true }),
        signal: ctrl.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const reader = res.body!.getReader();
      const dec = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';
        for (const line of lines) {
          const t = line.trim();
          if (!t.startsWith('data: ')) continue;
          const raw = t.slice(6);
          if (raw === '[DONE]') { onDone(); return; }
          try { onEvent(JSON.parse(raw)); } catch {}
        }
      }
      onDone();
    } catch (e: any) {
      if (e.name === 'AbortError') return;
      onError(e);
    }
  })();
  return () => ctrl.abort();
}

async function createSession(portalId: string, userId: string): Promise<string> {
  const res = await fetch(`${BASE_URL}/api/portals/${portalId}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId }),
  });
  const data = await res.json();
  return data.session_id;
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                      */
/* ------------------------------------------------------------------ */

const EventCard: React.FC<{ msg: ChatMsg }> = ({ msg }) => {
  const { eventType, meta } = msg;

  if (eventType === 'intent_understood' && meta?.intent) {
    const intent = meta.intent;
    return (
      <div className={styles.eventCard}>
        <div className={styles.eventTitle}>
          <span>🎯</span> 意图理解
        </div>
        <div style={{ marginBottom: 6 }}>{intent.understood_intent}</div>
        {intent.dispatch_plans?.length > 0 && (
          <div>
            <div style={{ marginBottom: 4, fontSize: '0.72rem' }}>将调用：</div>
            {intent.dispatch_plans.map((p: any, i: number) => (
              <span key={i} className={styles.eventChip}>
                ⚡ {p.workflow_name}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (eventType === 'workflow_dispatch_start') {
    return (
      <div className={styles.eventCard}>
        <div className={styles.eventTitle}>
          <span>▶</span>
          <span className={`${styles.eventChip} ${styles.eventChipBlue}`}>
            {meta?.workflow_name ?? '工作流'}
          </span>
          执行中…
        </div>
      </div>
    );
  }

  if (eventType === 'workflow_dispatch_result') {
    return (
      <div className={styles.eventCard}>
        <div className={styles.eventTitle}>
          <span>✅</span>
          <span className={styles.eventChip}>{meta?.workflow_name}</span>
          完成
        </div>
        {meta?.workflow_result && (
          <div style={{ marginTop: 4, whiteSpace: 'pre-wrap', fontSize: '0.76rem', opacity: 0.8 }}>
            {meta.workflow_result.slice(0, 300)}{meta.workflow_result.length > 300 ? '…' : ''}
          </div>
        )}
      </div>
    );
  }

  if (eventType === 'synthesis_start') {
    return (
      <div className={styles.eventCard}>
        <div className={styles.eventTitle}><span>✍️</span> 正在整合回答…</div>
      </div>
    );
  }

  return null;
};

/* ------------------------------------------------------------------ */
/*  Main Component                                                      */
/* ------------------------------------------------------------------ */

const PortalChat: React.FC<PortalChatProps> = ({ portal, onBack, hideBackButton }) => {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [activeWfs, setActiveWfs] = useState<string[]>([]);
  const [channelOpen, setChannelOpen] = useState(false);

  const userId = 'default';
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const assistantIdRef = useRef<string | null>(null);

  // Init session
  useEffect(() => {
    createSession(portal.id, userId).then(setSessionId).catch(console.error);
    return () => abortRef.current?.();
  }, [portal.id]);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const uid = () => Math.random().toString(36).slice(2);

  const appendMsg = (msg: Omit<ChatMsg, 'id'>) =>
    setMessages(prev => [...prev, { ...msg, id: uid() }]);

  const updateLastAssistant = (delta: string) => {
    if (!assistantIdRef.current) return;
    const targetId = assistantIdRef.current;
    setMessages(prev =>
      prev.map(m => m.id === targetId ? { ...m, content: m.content + delta } : m)
    );
  };

  const send = () => {
    if (!input.trim() || loading || !sessionId) return;
    const text = input.trim();
    setInput('');
    setLoading(true);
    setActiveWfs([]);

    appendMsg({ role: 'user', content: text });

    // placeholder for assistant
    const aId = uid();
    assistantIdRef.current = aId;
    setMessages(prev => [...prev, { id: aId, role: 'assistant', content: '' }]);

    abortRef.current = portalChat(
      portal.id,
      sessionId,
      text,
      userId,
      (event) => {
        const type = event.type;

        if (type === 'intent_understood') {
          appendMsg({ role: 'event', content: '', eventType: type, meta: event });
          const plans = event.intent?.dispatch_plans ?? [];
          setActiveWfs(plans.map((p: any) => p.workflow_id));
        } else if (type === 'workflow_dispatch_start') {
          appendMsg({ role: 'event', content: '', eventType: type, meta: event });
        } else if (type === 'workflow_dispatch_result') {
          appendMsg({ role: 'event', content: '', eventType: type, meta: event });
          setActiveWfs(prev => prev.filter(id => id !== event.workflow_id));
        } else if (type === 'synthesis_start') {
          appendMsg({ role: 'event', content: '', eventType: type, meta: event });
        } else if (type === 'content' && event.delta) {
          updateLastAssistant(event.delta);
        } else if (type === 'error') {
          updateLastAssistant(`\n\n❌ 错误：${event.error}`);
        }
      },
      () => {
        setLoading(false);
        assistantIdRef.current = null;
        setActiveWfs([]);
      },
      (err) => {
        updateLastAssistant(`\n\n❌ 请求失败：${err.message}`);
        setLoading(false);
        assistantIdRef.current = null;
      }
    );
  };

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const suggestions = [
    '你好，你能做什么？',
    '给我介绍一下你的能力',
    '帮我查一下最近的信息',
  ];

  return (
    <div className={styles.container}>
      {/* Top bar */}
      <div className={styles.topbar}>
        {!hideBackButton && <button className={styles.backBtn} onClick={onBack}>← 返回</button>}
        <div className={styles.topbarInfo}>
          <div className={styles.topbarTitle}>🧠 {portal.name}</div>
          <div className={styles.topbarMeta}>
            {portal.workflow_ids.length} 个工作流 · {portal.provider} / {portal.model}
          </div>
        </div>
        <button
          className={styles.backBtn}
          style={{ marginLeft: 10 }}
          onClick={() => setChannelOpen(true)}
        >
          🔌 渠道接入
        </button>
        {portal.memory_enabled && (
          <span className={`${styles.badge} ${styles.badgeGreen}`}>记忆开启</span>
        )}
        {!sessionId && <span className={`${styles.badge} ${styles.badgeBlue}`}>连接中…</span>}
      </div>

      {/* Body */}
      <div className={styles.body}>
        {/* Messages */}
        <div className={styles.messages}>
          {messages.length === 0 ? (
            <div className={styles.welcome}>
              <div className={styles.welcomeIcon}>🧠</div>
              <h3>{portal.name}</h3>
              <p>
                {portal.description || '这是一个超级入口，能理解你的需求并自动调用合适的工作流来帮助你。'}
              </p>
              <div className={styles.welcomeChips}>
                {suggestions.map(s => (
                  <div key={s} className={styles.welcomeChip} onClick={() => { setInput(s); }}>
                    {s}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <>
              {messages.map(msg => {
                if (msg.role === 'event') {
                  return <EventCard key={msg.id} msg={msg} />;
                }
                if (msg.role === 'user') {
                  return (
                    <div key={msg.id} className={`${styles.msgRow} ${styles.msgRowUser}`}>
                      <div className={`${styles.avatar} ${styles.avatarUser}`}>👤</div>
                      <div className={`${styles.bubble} ${styles.bubbleUser}`}>{msg.content}</div>
                    </div>
                  );
                }
                // assistant
                return (
                  <div key={msg.id} className={styles.msgRow}>
                    <div className={`${styles.avatar} ${styles.avatarAssistant}`}>🧠</div>
                    <div className={`${styles.bubble} ${styles.bubbleAssistant}`}>
                      {msg.content || (loading && assistantIdRef.current === msg.id ? (
                        <div className={styles.thinking}>
                          <div className={styles.dot} />
                          <div className={styles.dot} />
                          <div className={styles.dot} />
                        </div>
                      ) : '…')}
                    </div>
                  </div>
                );
              })}
            </>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Side panel: bound workflows */}
        <div className={styles.sidePanel}>
          <div className={styles.sidePanelHeader}>绑定的工作流</div>
          <div className={styles.sidePanelBody}>
            {portal.workflow_ids.length === 0 ? (
              <div style={{ fontSize: '0.8rem', color: 'var(--color-text-muted)', textAlign: 'center', marginTop: 20 }}>
                未绑定工作流
              </div>
            ) : (
              portal.workflow_ids.map(id => (
                <div
                  key={id}
                  className={`${styles.wfChip} ${activeWfs.includes(id) ? styles.wfChipActive : ''}`}
                >
                  {activeWfs.includes(id) ? '⚡ ' : ''}
                  <span style={{ fontSize: '0.75rem', fontFamily: 'monospace', opacity: 0.6 }}>
                    {id.slice(0, 8)}…
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Input */}
      <div className={styles.inputArea}>
        <div className={styles.inputRow}>
          <textarea
            className={styles.textarea}
            placeholder="输入消息…（Enter 发送，Shift+Enter 换行）"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKey}
            disabled={loading || !sessionId}
            rows={1}
          />
          <button
            className={styles.sendBtn}
            onClick={send}
            disabled={loading || !input.trim() || !sessionId}
            title="发送"
          >
            ↑
          </button>
        </div>
        <div className={styles.inputHint}>
          由 {portal.provider}/{portal.model} 驱动 · 绑定 {portal.workflow_ids.length} 个工作流
        </div>
      </div>

      {channelOpen && (
        <ChannelModal portalId={portal.id} onClose={() => setChannelOpen(false)} />
      )}
    </div>
  );
};

export default PortalChat;

const ChannelModal: React.FC<{ portalId: string; onClose: () => void }> = ({ portalId, onClose }) => {
  const [loading, setLoading] = useState(true);
  const [channels, setChannels] = useState<Record<string, any>>({});
  const [editing, setEditing] = useState<ChannelName | null>(null);

  const [telegramToken, setTelegramToken] = useState('');
  const [dingtalkClientId, setDingtalkClientId] = useState('');
  const [dingtalkClientSecret, setDingtalkClientSecret] = useState('');
  const [feishuAppId, setFeishuAppId] = useState('');
  const [feishuAppSecret, setFeishuAppSecret] = useState('');

  const [weixinLoginId, setWeixinLoginId] = useState<string | null>(null);
  const [weixinQrUrl, setWeixinQrUrl] = useState<string | null>(null);
  const [weixinStatus, setWeixinStatus] = useState<string>('');

  const refresh = async () => {
    setLoading(true);
    try {
      const data = await (api as any).getPortalChannels(portalId);
      setChannels(data || {});
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, [portalId]);

  useEffect(() => {
    if (!weixinLoginId) return;
    let stopped = false;
    const tick = async () => {
      try {
        const data = await (api as any).pollWeixinQrLogin(portalId, weixinLoginId);
        if (stopped) return;
        setWeixinStatus(data?.status || '');
        if (data?.status === 'confirmed') {
          await refresh();
          setWeixinLoginId(null);
          return;
        }
      } catch {}
      if (!stopped) setTimeout(tick, 1200);
    };
    tick();
    return () => { stopped = true; };
  }, [weixinLoginId, portalId]);

  const statusLine = (c: any) => {
    if (!c) return '未配置';
    if (!c.enabled) return '已禁用';
    if (c.connected) return '🟢 已连接';
    if (c.last_error) return `🔴 ${c.last_error}`;
    if (c.meta?.running) return '🟠 连接中…';
    return '🟠 未连接';
  };

  const saveTelegram = async () => {
    await (api as any).upsertPortalChannel(portalId, 'telegram', {
      enabled: true,
      config: { token: telegramToken },
    });
    setEditing(null);
    await refresh();
  };

  const saveDingTalk = async () => {
    await (api as any).upsertPortalChannel(portalId, 'dingtalk', {
      enabled: true,
      config: { client_id: dingtalkClientId, client_secret: dingtalkClientSecret },
    });
    setEditing(null);
    await refresh();
  };

  const saveFeishu = async () => {
    await (api as any).upsertPortalChannel(portalId, 'feishu', {
      enabled: true,
      config: { app_id: feishuAppId, app_secret: feishuAppSecret },
    });
    setEditing(null);
    await refresh();
  };

  const startWeixin = async () => {
    const data = await (api as any).startWeixinQrLogin(portalId);
    setWeixinLoginId(data?.login_id);
    setWeixinQrUrl(data?.qrcode_img_content || null);
    setWeixinStatus(data?.status || 'wait');
  };

  const unbind = async (channel: ChannelName) => {
    await (api as any).deletePortalChannel(portalId, channel);
    await refresh();
  };

  const row = (channel: ChannelName, title: string, extra?: React.ReactNode) => (
    <div style={{ border: '1px solid var(--color-secondary)', borderRadius: 10, padding: 12, marginTop: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <div>
          <div style={{ fontWeight: 700 }}>{title}</div>
          <div style={{ fontSize: '0.8rem', color: 'var(--color-text-muted)', marginTop: 4 }}>
            {statusLine(channels[channel])}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className={styles.backBtn} onClick={() => setEditing(channel)}>配置</button>
          <button className={styles.backBtn} onClick={() => unbind(channel)}>解绑</button>
        </div>
      </div>
      {extra}
    </div>
  );

  const editPanel = () => {
    if (!editing) return null;
    const panelStyle: React.CSSProperties = { marginTop: 10, padding: 12, borderRadius: 10, border: '1px solid var(--color-secondary)' };

    if (editing === 'telegram') {
      return (
        <div style={panelStyle}>
          <div style={{ fontWeight: 700, marginBottom: 8 }}>Telegram Token</div>
          <input
            style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--color-secondary)' }}
            placeholder="123456:ABC..."
            value={telegramToken}
            onChange={e => setTelegramToken(e.target.value)}
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <button className={styles.backBtn} onClick={saveTelegram}>保存并连接</button>
            <button className={styles.backBtn} onClick={() => setEditing(null)}>取消</button>
          </div>
        </div>
      );
    }

    if (editing === 'dingtalk') {
      return (
        <div style={panelStyle}>
          <div style={{ fontWeight: 700, marginBottom: 8 }}>钉钉 Stream 凭证</div>
          <input
            style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--color-secondary)' }}
            placeholder="Client ID"
            value={dingtalkClientId}
            onChange={e => setDingtalkClientId(e.target.value)}
          />
          <input
            style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--color-secondary)', marginTop: 8 }}
            placeholder="Client Secret"
            value={dingtalkClientSecret}
            onChange={e => setDingtalkClientSecret(e.target.value)}
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <button className={styles.backBtn} onClick={saveDingTalk}>保存并连接</button>
            <button className={styles.backBtn} onClick={() => setEditing(null)}>取消</button>
          </div>
        </div>
      );
    }

    if (editing === 'feishu') {
      return (
        <div style={panelStyle}>
          <div style={{ fontWeight: 700, marginBottom: 8 }}>飞书 App 凭证</div>
          <input
            style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--color-secondary)' }}
            placeholder="App ID (cli_xxx)"
            value={feishuAppId}
            onChange={e => setFeishuAppId(e.target.value)}
          />
          <input
            style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--color-secondary)', marginTop: 8 }}
            placeholder="App Secret"
            value={feishuAppSecret}
            onChange={e => setFeishuAppSecret(e.target.value)}
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <button className={styles.backBtn} onClick={saveFeishu}>保存并连接</button>
            <button className={styles.backBtn} onClick={() => setEditing(null)}>取消</button>
          </div>
        </div>
      );
    }

    return null;
  };

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(15, 23, 42, 0.35)', display: 'flex', justifyContent: 'center', alignItems: 'center', padding: 18, zIndex: 50 }} onClick={onClose}>
      <div style={{ width: '100%', maxWidth: 720, background: 'var(--color-primary)', border: '1px solid var(--color-secondary)', borderRadius: 14, padding: 18 }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ fontSize: '1.1rem', fontWeight: 800 }}>🔌 渠道接入</div>
            <div style={{ marginTop: 4, fontSize: '0.85rem', color: 'var(--color-text-muted)' }}>为该入口绑定社交软件 Bot；支持微信扫码</div>
          </div>
          <button className={styles.backBtn} onClick={refresh} disabled={loading}>刷新</button>
        </div>

        {loading ? (
          <div style={{ padding: 20 }}>加载中…</div>
        ) : (
          <>
            {row('weixin', '微信（扫码登录）', (
              <div style={{ marginTop: 10 }}>
                <button className={styles.backBtn} onClick={startWeixin} disabled={!!weixinLoginId}>开始扫码</button>
                {weixinLoginId && (
                  <div style={{ marginTop: 10 }}>
                    <div style={{ fontSize: '0.8rem', color: 'var(--color-text-muted)' }}>状态：{weixinStatus || 'wait'}</div>
                    {weixinQrUrl && (
                      <div style={{ marginTop: 8 }}>
                        <img src={weixinQrUrl} style={{ width: 220, height: 220, borderRadius: 10, border: '1px solid var(--color-secondary)' }} />
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
            {row('telegram', 'Telegram（Token）')}
            {row('dingtalk', '钉钉（Stream）')}
            {row('feishu', '飞书（WebSocket）')}
            {editPanel()}
          </>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 14 }}>
          <button className={styles.backBtn} onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
};
