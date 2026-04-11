import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import { api, ApprovalRecord, ExecutionEvent } from '../api/client';
import {
  applyApprovalEventToNodes,
  createNodeState,
  mergeApprovalIntoNodes,
  NodeState,
  setApprovalResolving,
} from './executionState';
import styles from './ExecutionPanel.module.css';
import { useToast } from './ToastProvider';

interface ExecutionPanelProps {
  visible: boolean;
  workflowId: string | null;
  workflowName: string;
  onClose: () => void;
}

type WorkflowStatus = 'idle' | 'running' | 'completed' | 'error';

const ExecutionPanel: React.FC<ExecutionPanelProps> = ({
  visible,
  workflowId,
  workflowName,
  onClose,
}) => {
  const [inputMessage, setInputMessage] = useState('');
  const [workflowStatus, setWorkflowStatus] = useState<WorkflowStatus>('idle');
  const [nodes, setNodes] = useState<Map<string, NodeState>>(new Map());
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [finalResult, setFinalResult] = useState<string>('');
  const [workflowError, setWorkflowError] = useState<string>('');
  const [executionId, setExecutionId] = useState<string>('');

  const cancelRef = useRef<(() => void) | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const toast = useToast();

  const getErrorMessage = useCallback((error: unknown) => {
    if (axios.isAxiosError(error)) {
      return (
        error.response?.data?.detail
        || error.response?.data?.message
        || error.message
      );
    }
    if (error instanceof Error) {
      return error.message;
    }
    return '请求失败';
  }, []);

  const loadApproval = useCallback(async (approvalId: string, nodeId: string) => {
    try {
      const approval = await api.getApproval(approvalId);
      setNodes((prev) => mergeApprovalIntoNodes(prev, nodeId, approval));
    } catch (error) {
      console.warn(`Failed to load approval ${approvalId}:`, error);
    }
  }, []);

  const handleResolveApproval = useCallback(async (
    nodeId: string,
    approvalId: string,
    approved: boolean,
  ) => {
    setNodes((prev) => setApprovalResolving(prev, nodeId, approvalId, true));

    try {
      const approval = approved
        ? await api.approveApproval(approvalId, { actor: 'ui' })
        : await api.denyApproval(approvalId, { actor: 'ui' });

      setNodes((prev) => mergeApprovalIntoNodes(prev, nodeId, approval));
      toast.success(
        approved ? '审批已通过' : '审批已拒绝',
        `${approval.tool_name} · ${approval.id.slice(0, 8)}`,
      );
    } catch (error) {
      setNodes((prev) => setApprovalResolving(prev, nodeId, approvalId, false));
      toast.error('审批操作失败', getErrorMessage(error));
    }
  }, [getErrorMessage, toast]);

  // Auto-scroll thinking content
  useEffect(() => {
    if (contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [nodes, selectedNodeId]);

  // Reset state when panel closes
  useEffect(() => {
    if (!visible) {
      // Keep state for review, but allow cancel
      if (cancelRef.current) {
        cancelRef.current();
        cancelRef.current = null;
      }
    }
  }, [visible]);

  const handleEvent = useCallback((event: ExecutionEvent) => {
    switch (event.event_type) {
      case 'workflow_start':
        setExecutionId(event.execution_id);
        setWorkflowStatus('running');
        setNodes(new Map());
        setFinalResult('');
        setWorkflowError('');
        break;

      case 'workflow_complete':
        setWorkflowStatus('completed');
        // Collect final result from all completed nodes
        setNodes(prev => {
          const allContent: string[] = [];
          prev.forEach(node => {
            if (node.status === 'completed' && node.content) {
              allContent.push(`【${node.name}】\n${node.content}`);
            }
          });
          if (allContent.length > 0) {
            setFinalResult(allContent.join('\n\n---\n\n'));
          }
          return prev;
        });
        break;

      case 'workflow_error':
        setWorkflowStatus('error');
        setWorkflowError(event.error || 'Unknown error');
        break;

      case 'node_start':
        const nextNode = createNodeState(event);
        if (nextNode) {
          setNodes(prev => {
            const newMap = new Map(prev);
            newMap.set(nextNode.id, nextNode);
            return newMap;
          });
          // Auto-select newly started node
          setSelectedNodeId(nextNode.id);
        }
        break;

      case 'node_thinking':
        if (event.node_id && event.delta_content) {
          setNodes(prev => {
            const newMap = new Map(prev);
            const node = newMap.get(event.node_id!);
            if (node) {
              newMap.set(event.node_id!, {
                ...node,
                content: node.content + event.delta_content,
              });
            }
            return newMap;
          });
        }
        break;

      case 'node_tool_call':
        if (event.node_id && event.tool_call) {
          setNodes(prev => {
            const newMap = new Map(prev);
            const node = newMap.get(event.node_id!);
            if (node) {
              newMap.set(event.node_id!, {
                ...node,
                toolCalls: [...node.toolCalls, event.tool_call!],
              });
            }
            return newMap;
          });
        }
        break;

      case 'node_tool_result':
        if (event.node_id && event.tool_result) {
          setNodes(prev => {
            const newMap = new Map(prev);
            const node = newMap.get(event.node_id!);
            if (node) {
              newMap.set(event.node_id!, {
                ...node,
                toolResults: [...node.toolResults, event.tool_result!],
              });
            }
            return newMap;
          });
        }
        break;

      case 'approval_required':
      case 'approval_resolved':
        if (event.node_id) {
          setNodes((prev) => applyApprovalEventToNodes(prev, event));
          setSelectedNodeId(event.node_id);
          const approvalId = event.metadata?.approval_id || event.tool_result?.metadata?.approval_id;
          if (typeof approvalId === 'string' && approvalId) {
            void loadApproval(approvalId, event.node_id);
          }
        }
        break;

      case 'node_complete':
        if (event.node_id) {
          setNodes(prev => {
            const newMap = new Map(prev);
            const node = newMap.get(event.node_id!);
            if (node) {
              newMap.set(event.node_id!, {
                ...node,
                status: 'completed',
                content: event.content || node.content,
                duration_ms: event.duration_ms,
              });
            }
            return newMap;
          });
        }
        break;

      case 'node_error':
        if (event.node_id) {
          setNodes(prev => {
            const newMap = new Map(prev);
            const node = newMap.get(event.node_id!);
            if (node) {
              newMap.set(event.node_id!, {
                ...node,
                status: 'error',
                error: event.error,
              });
            }
            return newMap;
          });
        }
        break;

      case 'routing_start':
        // Could visualize routing decisions
        break;
    }
  }, []);

  const handleRun = useCallback(() => {
    if (!workflowId || !inputMessage.trim()) return;

    // Reset state
    setNodes(new Map());
    setFinalResult('');
    setWorkflowError('');
    setSelectedNodeId(null);
    setWorkflowStatus('running');

    // Start streaming
    const cancel = api.runWorkflowStream(
      workflowId,
      inputMessage.trim(),
      handleEvent,
      (error) => {
        setWorkflowStatus('error');
        setWorkflowError(error.message);
      },
      () => {
        // Stream complete
        cancelRef.current = null;
      }
    );

    cancelRef.current = cancel;
  }, [workflowId, inputMessage, handleEvent]);

  const handleCancel = useCallback(() => {
    if (cancelRef.current) {
      cancelRef.current();
      cancelRef.current = null;
      setWorkflowStatus('idle');
    }
  }, []);

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && workflowStatus !== 'running') {
      e.preventDefault();
      handleRun();
    }
  };

  const selectedNode = selectedNodeId ? nodes.get(selectedNodeId) : null;
  const nodesList = Array.from(nodes.values()).sort((a, b) => {
    // Sort by depth, then by order of appearance
    if (a.depth !== b.depth) return a.depth - b.depth;
    return 0;
  });

  const getStatusClass = (status: NodeState['status']) => {
    switch (status) {
      case 'pending': return styles.statusPending;
      case 'running': return styles.statusRunning;
      case 'completed': return styles.statusCompleted;
      case 'error': return styles.statusError;
    }
  };

  const getWorkflowStatusClass = () => {
    switch (workflowStatus) {
      case 'idle': return styles.workflowStatusIdle;
      case 'running': return styles.workflowStatusRunning;
      case 'completed': return styles.workflowStatusCompleted;
      case 'error': return styles.workflowStatusError;
    }
  };

  const formatDuration = (ms?: number) => {
    if (ms === undefined) return '';
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  const formatDateTime = (value?: string | null) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString('zh-CN', { hour12: false });
  };

  const formatApprovalStatus = (status: ApprovalRecord['status']) => {
    switch (status) {
      case 'approved':
        return '已通过';
      case 'denied':
        return '已拒绝';
      default:
        return '待审批';
    }
  };

  const getApprovalBadgeClass = (status: ApprovalRecord['status']) => {
    switch (status) {
      case 'approved':
        return styles.approvalBadgeApproved;
      case 'denied':
        return styles.approvalBadgeDenied;
      default:
        return styles.approvalBadgePending;
    }
  };

  return (
    <>
      {/* Overlay */}
      <div
        className={`${styles.overlay} ${visible ? styles.overlayVisible : ''}`}
        onClick={onClose}
      />

      {/* Panel */}
      <div className={`${styles.panel} ${visible ? styles.panelVisible : ''}`}>
        {/* Header */}
        <div className={styles.panelHeader}>
          <div>
            <h3 className={styles.panelTitle}>
              运行: {workflowName || '工作流'}
            </h3>
            <span className={`${styles.workflowStatus} ${getWorkflowStatusClass()}`}>
              {workflowStatus === 'running' && '● '}
              {workflowStatus.charAt(0).toUpperCase() + workflowStatus.slice(1)}
              {executionId && workflowStatus !== 'idle' && (
                <span style={{ opacity: 0.6, marginLeft: 8 }}>ID: {executionId.slice(0, 8)}</span>
              )}
            </span>
          </div>
          <button className={styles.closeButton} onClick={onClose}>×</button>
        </div>

        {/* Body */}
        <div className={styles.panelBody}>
          {/* Input Section */}
          <div className={styles.inputSection}>
            <div className={styles.inputWrapper}>
              <textarea
                className={styles.inputTextarea}
                value={inputMessage}
                onChange={(e) => setInputMessage(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder="输入消息给工作流..."
                disabled={workflowStatus === 'running'}
                rows={2}
              />
              <div className={styles.inputButtons}>
                {workflowStatus === 'running' ? (
                  <button className={styles.cancelButton} onClick={handleCancel}>
                    取消
                  </button>
                ) : (
                  <button
                    className={styles.runButton}
                    onClick={handleRun}
                    disabled={!inputMessage.trim() || !workflowId}
                  >
                    运行
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Content Layout */}
          <div className={styles.contentLayout}>
            {/* Timeline */}
            <div className={styles.timelineSection}>
              <div className={styles.timelineHeader}>执行时间线</div>
              <div className={styles.timelineList}>
                {nodesList.length === 0 ? (
                  <div style={{ padding: '16px', color: '#666', textAlign: 'center' }}>
                    运行工作流以查看执行时间线
                  </div>
                ) : (
                  nodesList.map((node) => (
                    <div
                      key={node.id}
                      className={`${styles.timelineItem} ${selectedNodeId === node.id ? styles.timelineItemSelected : ''}`}
                      onClick={() => setSelectedNodeId(node.id)}
                      style={{ paddingLeft: `${16 + node.depth * 16}px` }}
                    >
                      <div className={styles.timelineItemHeader}>
                        <span className={`${styles.nodeStatus} ${getStatusClass(node.status)}`} />
                        <span className={styles.nodeName}>{node.name}</span>
                        {node.depth > 0 && (
                          <span className={styles.nodeDepth}>L{node.depth}</span>
                        )}
                      </div>
                      {node.duration_ms !== undefined && (
                        <div className={styles.nodeDuration}>
                          {formatDuration(node.duration_ms)}
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Detail View */}
            <div className={styles.detailSection}>
              {selectedNode ? (
                <>
                  <div className={styles.detailHeader}>
                    <h4 className={styles.detailTitle}>{selectedNode.name}</h4>
                    <div className={styles.detailMeta}>
                      状态: {selectedNode.status}
                      {selectedNode.duration_ms !== undefined && (
                        <> · 耗时: {formatDuration(selectedNode.duration_ms)}</>
                      )}
                    </div>
                  </div>
                  <div className={styles.detailContent} ref={contentRef}>
                    {/* Thinking Content */}
                    {selectedNode.content && (
                      <div className={styles.thinkingContent}>
                        {selectedNode.content}
                        {selectedNode.status === 'running' && (
                          <span className={styles.cursor} />
                        )}
                      </div>
                    )}

                    {/* Tool Calls */}
                    {selectedNode.toolCalls.length > 0 && (
                      <div className={styles.toolCallsSection}>
                        <div className={styles.sectionTitle}>工具调用</div>
                        {selectedNode.toolCalls.map((tc, idx) => (
                          <div key={idx} className={styles.toolCallCard}>
                            <div className={styles.toolCallHeader}>
                              <span className={styles.toolCallIcon}>🔧</span>
                              <span className={styles.toolCallName}>{tc.name}</span>
                            </div>
                            <pre className={styles.toolCallArgs}>
                              {JSON.stringify(tc.arguments, null, 2)}
                            </pre>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Tool Results */}
                    {selectedNode.toolResults.length > 0 && (
                      <div className={styles.toolCallsSection}>
                        <div className={styles.sectionTitle}>工具结果</div>
                        {selectedNode.toolResults.map((tr, idx) => (
                          <div key={idx} className={styles.toolResultCard}>
                            <pre className={styles.toolResultContent}>
                              {tr.content}
                            </pre>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Approvals */}
                    {selectedNode.approvals.length > 0 && (
                      <div className={styles.toolCallsSection}>
                        <div className={styles.sectionTitle}>审批请求</div>
                        {selectedNode.approvals.map((approval) => (
                          <div key={approval.id} className={styles.approvalCard}>
                            <div className={styles.approvalHeader}>
                              <div>
                                <div className={styles.approvalTitle}>{approval.tool_name}</div>
                                <div className={styles.approvalMeta}>
                                  {approval.tool_source} · ID: {approval.id.slice(0, 8)} · 请求时间: {formatDateTime(approval.requested_at)}
                                </div>
                              </div>
                              <span className={`${styles.approvalBadge} ${getApprovalBadgeClass(approval.status)}`}>
                                {formatApprovalStatus(approval.status)}
                              </span>
                            </div>

                            <div className={styles.approvalSummary}>
                              工具调用: {approval.tool_call_id}
                              {approval.is_dangerous ? ' · 高风险' : ''}
                              {approval.reason ? ` · 原因: ${approval.reason}` : ''}
                            </div>

                            {Object.keys(approval.arguments || {}).length > 0 && (
                              <pre className={styles.approvalArgs}>
                                {JSON.stringify(approval.arguments, null, 2)}
                              </pre>
                            )}

                            {(approval.decision_by || approval.decision_comment || approval.resolved_at) && (
                              <div className={styles.approvalDecision}>
                                处理结果: {approval.decision_by || '系统'}
                                {approval.resolved_at ? ` · ${formatDateTime(approval.resolved_at)}` : ''}
                                {approval.decision_comment ? ` · ${approval.decision_comment}` : ''}
                              </div>
                            )}

                            {approval.status === 'pending' && (
                              <div className={styles.approvalActions}>
                                <button
                                  className={styles.approveButton}
                                  onClick={() => handleResolveApproval(selectedNode.id, approval.id, true)}
                                  disabled={approval.isResolving}
                                >
                                  {approval.isResolving ? '处理中...' : '批准'}
                                </button>
                                <button
                                  className={styles.denyButton}
                                  onClick={() => handleResolveApproval(selectedNode.id, approval.id, false)}
                                  disabled={approval.isResolving}
                                >
                                  {approval.isResolving ? '处理中...' : '拒绝'}
                                </button>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Error */}
                    {selectedNode.error && (
                      <div className={styles.errorSection}>
                        <div className={styles.errorTitle}>
                          ⚠️ 错误
                        </div>
                        <div className={styles.errorContent}>
                          {selectedNode.error}
                        </div>
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <div className={styles.emptyState}>
                  <div className={styles.emptyStateIcon}>📊</div>
                  <p>选择时间线中的节点查看详情</p>
                  {finalResult && (
                    <div className={styles.finalResultSection} style={{ marginTop: 24, textAlign: 'left', width: '100%' }}>
                      <div className={styles.finalResultTitle}>
                        ✅ 最终结果
                      </div>
                      <div className={styles.finalResultContent}>
                        {finalResult}
                      </div>
                    </div>
                  )}
                  {workflowError && (
                    <div className={styles.errorSection} style={{ marginTop: 24, textAlign: 'left', width: '100%' }}>
                      <div className={styles.errorTitle}>⚠️ 工作流错误</div>
                      <div className={styles.errorContent}>{workflowError}</div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

export default ExecutionPanel;
