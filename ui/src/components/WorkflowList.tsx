import React, { useEffect, useState } from 'react';
import { Card, Table, Button, Space, Tag, Modal, Form, Input, message } from 'antd';
import { PlusOutlined, PlayCircleOutlined, DeleteOutlined } from '@ant-design/icons';
import { api } from '../api/client';

interface Workflow {
  id: string;
  name: string;
  description: string;
  state: string;
  agent_count: number;
  created_at: string;
  updated_at: string;
}

interface WorkflowListProps {
  onSelect: (id: string) => void;
}

const stateColors: Record<string, string> = {
  created: 'default',
  initializing: 'processing',
  ready: 'success',
  running: 'processing',
  completed: 'success',
  failed: 'error',
  cancelled: 'warning',
};

const WorkflowList: React.FC<WorkflowListProps> = ({ onSelect }) => {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(false);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => {
    loadWorkflows();
  }, []);

  const loadWorkflows = async () => {
    setLoading(true);
    try {
      const data = await api.listWorkflows();
      setWorkflows(data);
    } catch (error) {
      message.error('Failed to load workflows');
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async (values: any) => {
    try {
      await api.createWorkflow(values);
      message.success('Workflow created');
      setIsCreateModalOpen(false);
      form.resetFields();
      loadWorkflows();
    } catch (error) {
      message.error('Failed to create workflow');
    }
  };

  const handleDelete = async (id: string) => {
    Modal.confirm({
      title: 'Delete Workflow',
      content: 'Are you sure you want to delete this workflow?',
      onOk: async () => {
        try {
          await api.deleteWorkflow(id);
          message.success('Workflow deleted');
          loadWorkflows();
        } catch (error) {
          message.error('Failed to delete workflow');
        }
      },
    });
  };

  const handleRun = async (id: string) => {
    const input = prompt('Enter your message:');
    if (!input) return;

    try {
      const result = await api.runWorkflow(id, input);
      message.success(`Workflow executed: ${result.state}`);
      if (result.output) {
        Modal.info({
          title: 'Output',
          content: <pre style={{ maxHeight: 400, overflow: 'auto' }}>{result.output}</pre>,
          width: 600,
        });
      }
    } catch (error) {
      message.error('Failed to run workflow');
    }
  };

  const columns = [
    {
      title: 'Name',
      dataIndex: 'name',
      key: 'name',
      render: (text: string, record: Workflow) => (
        <a onClick={() => onSelect(record.id)}>{text}</a>
      ),
    },
    {
      title: 'Description',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
    },
    {
      title: 'State',
      dataIndex: 'state',
      key: 'state',
      render: (state: string) => (
        <Tag color={stateColors[state] || 'default'}>{state}</Tag>
      ),
    },
    {
      title: 'Agents',
      dataIndex: 'agent_count',
      key: 'agent_count',
    },
    {
      title: 'Updated',
      dataIndex: 'updated_at',
      key: 'updated_at',
      render: (text: string) => new Date(text).toLocaleString(),
    },
    {
      title: 'Actions',
      key: 'actions',
      render: (_: any, record: Workflow) => (
        <Space>
          <Button
            type="link"
            icon={<PlayCircleOutlined />}
            onClick={() => handleRun(record.id)}
          >
            Run
          </Button>
          <Button
            type="link"
            danger
            icon={<DeleteOutlined />}
            onClick={() => handleDelete(record.id)}
          >
            Delete
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="Workflows"
      extra={
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setIsCreateModalOpen(true)}
        >
          Create Workflow
        </Button>
      }
    >
      <Table
        columns={columns}
        dataSource={workflows}
        rowKey="id"
        loading={loading}
      />

      <Modal
        title="Create Workflow"
        open={isCreateModalOpen}
        onCancel={() => setIsCreateModalOpen(false)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input placeholder="Workflow name" />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <Input.TextArea placeholder="Workflow description" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
};

export default WorkflowList;
