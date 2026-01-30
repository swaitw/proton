import axios from 'axios';

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const client = axios.create({
  baseURL: BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export interface Workflow {
  id: string;
  name: string;
  description: string;
  state: string;
  agent_count: number;
  created_at: string;
  updated_at: string;
}

export interface WorkflowDetail {
  id: string;
  name: string;
  description: string;
  config: any;
  tree: {
    root_id: string;
    nodes: Record<string, any>;
  };
  state: string;
}

export interface ExecutionResult {
  workflow_id: string;
  execution_id: string;
  state: string;
  output?: string;
  error?: string;
  duration_ms?: number;
}

export const api = {
  // Workflows
  async listWorkflows(): Promise<Workflow[]> {
    const response = await client.get('/api/workflows');
    return response.data;
  },

  async getWorkflow(id: string): Promise<WorkflowDetail> {
    const response = await client.get(`/api/workflows/${id}`);
    return response.data;
  },

  async createWorkflow(data: { name: string; description?: string }): Promise<Workflow> {
    const response = await client.post('/api/workflows', data);
    return response.data;
  },

  async deleteWorkflow(id: string): Promise<void> {
    await client.delete(`/api/workflows/${id}`);
  },

  async runWorkflow(id: string, message: string): Promise<ExecutionResult> {
    const response = await client.post(`/api/workflows/${id}/run`, {
      message,
      stream: false,
    });
    return response.data;
  },

  // Agents
  async addAgent(
    workflowId: string,
    data: {
      name: string;
      description?: string;
      type?: string;
      parent_id?: string;
      routing_strategy?: string;
    }
  ): Promise<any> {
    const response = await client.post(`/api/workflows/${workflowId}/agents`, data);
    return response.data;
  },

  async listAgents(workflowId: string): Promise<any[]> {
    const response = await client.get(`/api/workflows/${workflowId}/agents`);
    return response.data;
  },

  async removeAgent(workflowId: string, agentId: string): Promise<void> {
    await client.delete(`/api/workflows/${workflowId}/agents/${agentId}`);
  },

  // Plugins
  async listPlugins(): Promise<any[]> {
    const response = await client.get('/api/plugins');
    return response.data;
  },

  async registerMCP(data: {
    name: string;
    command: string;
    args?: string[];
    agent_id?: string;
  }): Promise<any> {
    const response = await client.post('/api/plugins/mcp', data);
    return response.data;
  },

  async registerSkill(data: {
    name: string;
    description: string;
    module_path: string;
    function_name: string;
    agent_id?: string;
  }): Promise<any> {
    const response = await client.post('/api/plugins/skill', data);
    return response.data;
  },

  async registerRAG(data: {
    name: string;
    type?: string;
    connection_string?: string;
    agent_id?: string;
  }): Promise<any> {
    const response = await client.post('/api/plugins/rag', data);
    return response.data;
  },

  async removePlugin(id: string): Promise<void> {
    await client.delete(`/api/plugins/${id}`);
  },
};
