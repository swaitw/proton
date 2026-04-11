import { describe, expect, it } from 'vitest';
import type { ApprovalRecord, ExecutionEvent } from '../api/client';
import {
  applyApprovalEventToNodes,
  createNodeState,
  mergeApprovalIntoNodes,
  setApprovalResolving,
} from './executionState';

function createBaseNodeMap() {
  const nodeStartEvent: ExecutionEvent = {
    event_type: 'node_start',
    timestamp: 1710000000,
    workflow_id: 'wf-1',
    execution_id: 'exec-1',
    node_id: 'node-1',
    node_name: '审批节点',
    depth: 0,
    metadata: {},
  };
  const node = createNodeState(nodeStartEvent);
  if (!node) {
    throw new Error('node should exist');
  }

  node.toolCalls.push({
    id: 'tc-1',
    name: 'send_email',
    arguments: { to: 'demo@example.com', subject: '审批测试' },
  });

  return new Map([[node.id, node]]);
}

describe('executionState approval helpers', () => {
  it('在 approval_required 事件到达时创建待审批记录', () => {
    const nodes = createBaseNodeMap();

    const event: ExecutionEvent = {
      event_type: 'approval_required',
      timestamp: 1710000010,
      workflow_id: 'wf-1',
      execution_id: 'exec-1',
      node_id: 'node-1',
      node_name: '审批节点',
      depth: 0,
      tool_result: {
        tool_call_id: 'tc-1',
        content: 'Approval required',
        is_error: true,
        metadata: {
          approval_id: 'approval-1',
          approval_status: 'pending',
          tool_name: 'send_email',
          tool_source: 'system',
        },
      },
      metadata: {
        approval_id: 'approval-1',
        approval_status: 'pending',
        tool_name: 'send_email',
        tool_source: 'system',
      },
    };

    const next = applyApprovalEventToNodes(nodes, event);
    const approval = next.get('node-1')?.approvals[0];

    expect(approval).toBeDefined();
    expect(approval?.id).toBe('approval-1');
    expect(approval?.status).toBe('pending');
    expect(approval?.tool_name).toBe('send_email');
    expect(approval?.arguments).toEqual({ to: 'demo@example.com', subject: '审批测试' });
  });

  it('在 approval_resolved 事件到达时更新审批状态', () => {
    const nodes = createBaseNodeMap();
    const pending = applyApprovalEventToNodes(nodes, {
      event_type: 'approval_required',
      timestamp: 1710000010,
      workflow_id: 'wf-1',
      execution_id: 'exec-1',
      node_id: 'node-1',
      node_name: '审批节点',
      depth: 0,
      tool_result: {
        tool_call_id: 'tc-1',
        content: 'Approval required',
        is_error: true,
        metadata: {
          approval_id: 'approval-1',
          approval_status: 'pending',
          tool_name: 'send_email',
          tool_source: 'system',
        },
      },
      metadata: {
        approval_id: 'approval-1',
        approval_status: 'pending',
        tool_name: 'send_email',
        tool_source: 'system',
      },
    });

    const resolved = applyApprovalEventToNodes(pending, {
      event_type: 'approval_resolved',
      timestamp: 1710000020,
      workflow_id: 'wf-1',
      execution_id: 'exec-1',
      node_id: 'node-1',
      node_name: '审批节点',
      depth: 0,
      tool_result: {
        tool_call_id: 'tc-1',
        content: 'Approval approved',
        is_error: false,
        metadata: {
          approval_id: 'approval-1',
          approval_status: 'approved',
          tool_name: 'send_email',
          tool_source: 'system',
          decision_by: 'qa-user',
        },
      },
      metadata: {
        approval_id: 'approval-1',
        approval_status: 'approved',
        tool_name: 'send_email',
        tool_source: 'system',
        decision_by: 'qa-user',
      },
    });

    const approval = resolved.get('node-1')?.approvals[0];
    expect(approval?.status).toBe('approved');
    expect(approval?.decision_by).toBe('qa-user');
    expect(approval?.resolved_at).toBeTruthy();
  });

  it('合并审批 API 返回并清除 resolving 标记', () => {
    const nodes = createBaseNodeMap();
    const pending = applyApprovalEventToNodes(nodes, {
      event_type: 'approval_required',
      timestamp: 1710000010,
      workflow_id: 'wf-1',
      execution_id: 'exec-1',
      node_id: 'node-1',
      node_name: '审批节点',
      depth: 0,
      tool_result: {
        tool_call_id: 'tc-1',
        content: 'Approval required',
        is_error: true,
        metadata: {
          approval_id: 'approval-1',
          approval_status: 'pending',
          tool_name: 'send_email',
          tool_source: 'system',
        },
      },
      metadata: {
        approval_id: 'approval-1',
        approval_status: 'pending',
        tool_name: 'send_email',
        tool_source: 'system',
      },
    });

    const resolving = setApprovalResolving(pending, 'node-1', 'approval-1', true);

    const record: ApprovalRecord = {
      id: 'approval-1',
      status: 'approved',
      workflow_id: 'wf-1',
      execution_id: 'exec-1',
      node_id: 'node-1',
      node_name: '审批节点',
      tool_call_id: 'tc-1',
      tool_name: 'send_email',
      tool_source: 'system',
      arguments: { to: 'demo@example.com', subject: '审批测试' },
      approval_required: true,
      is_dangerous: false,
      reason: 'approval_required',
      requested_by: 'system',
      requested_at: '2026-04-10T10:00:00.000Z',
      updated_at: '2026-04-10T10:01:00.000Z',
      resolved_at: '2026-04-10T10:01:00.000Z',
      decision_by: 'ui',
      decision_comment: 'ok',
    };

    const merged = mergeApprovalIntoNodes(resolving, 'node-1', record);
    const approval = merged.get('node-1')?.approvals[0];

    expect(approval?.status).toBe('approved');
    expect(approval?.decision_comment).toBe('ok');
    expect(approval?.isResolving).toBe(false);
  });

  it('模拟 SSE 审批链路：required -> resolving -> resolved -> API merge', () => {
    const nodes = createBaseNodeMap();
    const required = applyApprovalEventToNodes(nodes, {
      event_type: 'approval_required',
      timestamp: 1710000010,
      workflow_id: 'wf-1',
      execution_id: 'exec-1',
      node_id: 'node-1',
      node_name: '审批节点',
      depth: 0,
      tool_result: {
        tool_call_id: 'tc-1',
        content: 'Approval required',
        is_error: true,
        metadata: {
          approval_id: 'approval-1',
          approval_status: 'pending',
          tool_name: 'send_email',
          tool_source: 'system',
        },
      },
      metadata: {
        approval_id: 'approval-1',
        approval_status: 'pending',
        tool_name: 'send_email',
        tool_source: 'system',
      },
    });

    const resolving = setApprovalResolving(required, 'node-1', 'approval-1', true);
    expect(resolving.get('node-1')?.approvals[0].isResolving).toBe(true);

    const resolvedByEvent = applyApprovalEventToNodes(resolving, {
      event_type: 'approval_resolved',
      timestamp: 1710000020,
      workflow_id: 'wf-1',
      execution_id: 'exec-1',
      node_id: 'node-1',
      node_name: '审批节点',
      depth: 0,
      tool_result: {
        tool_call_id: 'tc-1',
        content: 'Approval approved',
        is_error: false,
        metadata: {
          approval_id: 'approval-1',
          approval_status: 'approved',
          tool_name: 'send_email',
          tool_source: 'system',
          decision_by: 'event-user',
        },
      },
      metadata: {
        approval_id: 'approval-1',
        approval_status: 'approved',
        tool_name: 'send_email',
        tool_source: 'system',
        decision_by: 'event-user',
      },
    });

    const apiRecord: ApprovalRecord = {
      id: 'approval-1',
      status: 'approved',
      workflow_id: 'wf-1',
      execution_id: 'exec-1',
      node_id: 'node-1',
      node_name: '审批节点',
      tool_call_id: 'tc-1',
      tool_name: 'send_email',
      tool_source: 'system',
      arguments: { to: 'demo@example.com', subject: '审批测试' },
      approval_required: true,
      is_dangerous: false,
      reason: 'approval_required',
      requested_by: 'system',
      requested_at: '2026-04-10T10:00:00.000Z',
      updated_at: '2026-04-10T10:02:00.000Z',
      resolved_at: '2026-04-10T10:02:00.000Z',
      decision_by: 'api-user',
      decision_comment: 'merged',
    };

    const merged = mergeApprovalIntoNodes(resolvedByEvent, 'node-1', apiRecord);
    const approval = merged.get('node-1')?.approvals[0];
    expect(approval?.status).toBe('approved');
    expect(approval?.decision_by).toBe('api-user');
    expect(approval?.decision_comment).toBe('merged');
    expect(approval?.isResolving).toBe(false);
  });
});
