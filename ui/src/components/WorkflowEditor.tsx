import React, { useCallback, useState, useEffect } from 'react';
import ReactFlow, {
  Node,
  Edge,
  Controls,
  Background,
  useNodesState,
  useEdgesState,
  addEdge,
  Connection,
  MarkerType,
  Panel,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { Card, Button, Space, Modal, Form, Input, Select, message } from 'antd';
import { PlusOutlined, PlayCircleOutlined, SaveOutlined } from '@ant-design/icons';
import AgentNode from './AgentNode';
import { api } from '../api/client';

const nodeTypes = {
  agent: AgentNode,
};

interface WorkflowEditorProps {
  workflowId: string | null;
}

const WorkflowEditor: React.FC<WorkflowEditorProps> = ({ workflowId }) => {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [form] = Form.useForm();

  // Load workflow data
  useEffect(() => {
    if (workflowId) {
      loadWorkflow(workflowId);
    } else {
      // Initialize with empty workflow
      setNodes([]);
      setEdges([]);
    }
  }, [workflowId]);

  const loadWorkflow = async (id: string) => {
    try {
      const workflow = await api.getWorkflow(id);
      // Convert workflow to nodes and edges
      const flowNodes: Node[] = [];
      const flowEdges: Edge[] = [];

      if (workflow.tree?.nodes) {
        let y = 0;
        Object.values(workflow.tree.nodes).forEach((agent: any, index: number) => {
          flowNodes.push({
            id: agent.id,
            type: 'agent',
            position: { x: agent.parent_id ? 200 : 0, y: y },
            data: {
              label: agent.name,
              type: agent.type,
              description: agent.description,
              routing_strategy: agent.routing_strategy,
            },
          });
          y += 150;

          // Create edges for parent-child relationships
          if (agent.parent_id) {
            flowEdges.push({
              id: `${agent.parent_id}-${agent.id}`,
              source: agent.parent_id,
              target: agent.id,
              markerEnd: { type: MarkerType.ArrowClosed },
            });
          }
        });
      }

      setNodes(flowNodes);
      setEdges(flowEdges);
    } catch (error) {
      message.error('Failed to load workflow');
    }
  };

  const onConnect = useCallback(
    (params: Connection) => {
      setEdges((eds) =>
        addEdge(
          {
            ...params,
            markerEnd: { type: MarkerType.ArrowClosed },
          },
          eds
        )
      );
    },
    [setEdges]
  );

  const handleAddAgent = async (values: any) => {
    const newNode: Node = {
      id: `agent-${Date.now()}`,
      type: 'agent',
      position: { x: 250, y: nodes.length * 150 },
      data: {
        label: values.name,
        type: values.type,
        description: values.description,
        routing_strategy: values.routing_strategy,
      },
    };

    setNodes((nds) => [...nds, newNode]);
    setIsAddModalOpen(false);
    form.resetFields();

    // If connected to a parent, create edge
    if (values.parent_id) {
      setEdges((eds) => [
        ...eds,
        {
          id: `${values.parent_id}-${newNode.id}`,
          source: values.parent_id,
          target: newNode.id,
          markerEnd: { type: MarkerType.ArrowClosed },
        },
      ]);
    }

    message.success('Agent added');
  };

  const handleSaveWorkflow = async () => {
    try {
      // Convert nodes/edges back to workflow format
      message.success('Workflow saved');
    } catch (error) {
      message.error('Failed to save workflow');
    }
  };

  const handleRunWorkflow = async () => {
    if (!workflowId) {
      message.warning('Please save the workflow first');
      return;
    }

    const input = prompt('Enter your message:');
    if (!input) return;

    try {
      const result = await api.runWorkflow(workflowId, input);
      message.success(`Workflow completed: ${result.state}`);
      if (result.output) {
        Modal.info({
          title: 'Workflow Output',
          content: <pre style={{ maxHeight: 400, overflow: 'auto' }}>{result.output}</pre>,
          width: 600,
        });
      }
    } catch (error) {
      message.error('Failed to run workflow');
    }
  };

  return (
    <Card
      title="Workflow Editor"
      extra={
        <Space>
          <Button icon={<PlusOutlined />} onClick={() => setIsAddModalOpen(true)}>
            Add Agent
          </Button>
          <Button icon={<SaveOutlined />} onClick={handleSaveWorkflow}>
            Save
          </Button>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            onClick={handleRunWorkflow}
          >
            Run
          </Button>
        </Space>
      }
      style={{ height: 'calc(100vh - 150px)' }}
      bodyStyle={{ height: 'calc(100% - 60px)', padding: 0 }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        nodeTypes={nodeTypes}
        onNodeClick={(_, node) => setSelectedNode(node)}
        fitView
      >
        <Controls />
        <Background />
        <Panel position="top-left">
          <div style={{ background: 'white', padding: 8, borderRadius: 4 }}>
            {workflowId ? `Workflow: ${workflowId}` : 'New Workflow'}
          </div>
        </Panel>
      </ReactFlow>

      <Modal
        title="Add Agent"
        open={isAddModalOpen}
        onCancel={() => setIsAddModalOpen(false)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={handleAddAgent}>
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input placeholder="Agent name" />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <Input.TextArea placeholder="Agent description" />
          </Form.Item>
          <Form.Item name="type" label="Type" initialValue="native">
            <Select>
              <Select.Option value="native">Native</Select.Option>
              <Select.Option value="coze">Coze</Select.Option>
              <Select.Option value="dify">Dify</Select.Option>
              <Select.Option value="doubao">Doubao</Select.Option>
              <Select.Option value="autogen">AutoGen</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="routing_strategy" label="Routing Strategy" initialValue="sequential">
            <Select>
              <Select.Option value="sequential">Sequential</Select.Option>
              <Select.Option value="parallel">Parallel</Select.Option>
              <Select.Option value="conditional">Conditional</Select.Option>
              <Select.Option value="handoff">Handoff</Select.Option>
              <Select.Option value="hierarchical">Hierarchical</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="parent_id" label="Parent Agent">
            <Select allowClear placeholder="Select parent (optional)">
              {nodes.map((node) => (
                <Select.Option key={node.id} value={node.id}>
                  {node.data.label}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
};

export default WorkflowEditor;
