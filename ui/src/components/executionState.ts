import type { ApprovalRecord, ExecutionEvent, ToolCallData } from '../api/client';

export interface ApprovalViewState extends ApprovalRecord {
  isResolving?: boolean;
}

export interface NodeState {
  id: string;
  name: string;
  status: 'pending' | 'running' | 'completed' | 'error';
  depth: number;
  content: string;
  toolCalls: ToolCallData[];
  toolResults: Array<{ tool_call_id: string; content: string; is_error: boolean }>;
  approvals: ApprovalViewState[];
  duration_ms?: number;
  error?: string;
}

function toIsoTimestamp(timestamp?: number): string {
  if (typeof timestamp !== 'number' || Number.isNaN(timestamp)) {
    return new Date().toISOString();
  }
  return new Date(timestamp * 1000).toISOString();
}

function readApprovalMetadata(event: ExecutionEvent): Record<string, any> {
  return {
    ...(event.tool_result?.metadata || {}),
    ...(event.metadata || {}),
  };
}

function normalizeApprovalStatus(value: unknown): ApprovalRecord['status'] {
  if (value === 'approved' || value === 'denied') {
    return value;
  }
  return 'pending';
}

export function createNodeState(event: ExecutionEvent): NodeState | null {
  if (!event.node_id || !event.node_name) {
    return null;
  }

  return {
    id: event.node_id,
    name: event.node_name,
    status: 'running',
    depth: event.depth || 0,
    content: '',
    toolCalls: [],
    toolResults: [],
    approvals: [],
  };
}

export function buildApprovalFromEvent(
  event: ExecutionEvent,
  node?: NodeState,
): ApprovalViewState | null {
  const metadata = readApprovalMetadata(event);
  const approvalId = metadata.approval_id;
  const toolResult = event.tool_result;

  if (!event.node_id || !toolResult || typeof approvalId !== 'string' || !approvalId) {
    return null;
  }

  const matchedToolCall = node?.toolCalls.find(
    (toolCall) => toolCall.id === toolResult.tool_call_id,
  );
  const status = normalizeApprovalStatus(metadata.approval_status);
  const timestamp = toIsoTimestamp(event.timestamp);

  return {
    id: approvalId,
    status,
    workflow_id: event.workflow_id,
    execution_id: event.execution_id,
    node_id: event.node_id,
    node_name: event.node_name ?? node?.name ?? null,
    tool_call_id: toolResult.tool_call_id,
    tool_name:
      typeof metadata.tool_name === 'string' && metadata.tool_name
        ? metadata.tool_name
        : matchedToolCall?.name || 'unknown',
    tool_source:
      typeof metadata.tool_source === 'string' && metadata.tool_source
        ? metadata.tool_source
        : 'unknown',
    arguments: matchedToolCall?.arguments || {},
    approval_required: true,
    is_dangerous: Boolean(metadata.is_dangerous),
    reason:
      typeof metadata.reason === 'string' && metadata.reason ? metadata.reason : null,
    requested_by:
      typeof metadata.requested_by === 'string' && metadata.requested_by
        ? metadata.requested_by
        : null,
    requested_at: timestamp,
    updated_at: timestamp,
    resolved_at: status === 'pending' ? null : timestamp,
    decision_by:
      typeof metadata.decision_by === 'string' && metadata.decision_by
        ? metadata.decision_by
        : null,
    decision_comment:
      typeof metadata.decision_comment === 'string' && metadata.decision_comment
        ? metadata.decision_comment
        : null,
    isResolving: false,
  };
}

function upsertApprovals(
  approvals: ApprovalViewState[],
  approval: ApprovalViewState,
): ApprovalViewState[] {
  const existing = approvals.find((item) => item.id === approval.id);
  if (!existing) {
    return [approval, ...approvals];
  }

  return approvals.map((item) =>
    item.id === approval.id
      ? {
          ...item,
          ...approval,
          isResolving: approval.isResolving ?? item.isResolving ?? false,
        }
      : item,
  );
}

export function applyApprovalEventToNodes(
  nodes: Map<string, NodeState>,
  event: ExecutionEvent,
): Map<string, NodeState> {
  if (!event.node_id) {
    return nodes;
  }

  const node = nodes.get(event.node_id);
  if (!node) {
    return nodes;
  }

  const approval = buildApprovalFromEvent(event, node);
  if (!approval) {
    return nodes;
  }

  const next = new Map(nodes);
  next.set(event.node_id, {
    ...node,
    approvals: upsertApprovals(node.approvals, approval),
  });
  return next;
}

export function mergeApprovalIntoNodes(
  nodes: Map<string, NodeState>,
  nodeId: string,
  approval: ApprovalRecord,
): Map<string, NodeState> {
  const node = nodes.get(nodeId);
  if (!node) {
    return nodes;
  }

  const next = new Map(nodes);
  next.set(nodeId, {
    ...node,
    approvals: upsertApprovals(node.approvals, {
      ...approval,
      isResolving: false,
    }),
  });
  return next;
}

export function setApprovalResolving(
  nodes: Map<string, NodeState>,
  nodeId: string,
  approvalId: string,
  isResolving: boolean,
): Map<string, NodeState> {
  const node = nodes.get(nodeId);
  if (!node) {
    return nodes;
  }

  const next = new Map(nodes);
  next.set(nodeId, {
    ...node,
    approvals: node.approvals.map((approval) =>
      approval.id === approvalId ? { ...approval, isResolving } : approval,
    ),
  });
  return next;
}
