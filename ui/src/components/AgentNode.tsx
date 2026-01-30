import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { Tag } from 'antd';
import { RobotOutlined } from '@ant-design/icons';

interface AgentNodeData {
  label: string;
  type: string;
  description?: string;
  routing_strategy?: string;
}

const typeColors: Record<string, string> = {
  native: 'blue',
  coze: 'green',
  dify: 'orange',
  doubao: 'purple',
  autogen: 'cyan',
};

const AgentNode: React.FC<NodeProps<AgentNodeData>> = ({ data, selected }) => {
  return (
    <div
      style={{
        padding: 10,
        borderRadius: 8,
        background: 'white',
        border: `2px solid ${selected ? '#1890ff' : '#ddd'}`,
        minWidth: 180,
        boxShadow: selected ? '0 0 10px rgba(24, 144, 255, 0.3)' : '0 2px 4px rgba(0,0,0,0.1)',
      }}
    >
      <Handle type="target" position={Position.Top} />

      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
        <RobotOutlined style={{ marginRight: 8, fontSize: 18 }} />
        <strong>{data.label}</strong>
      </div>

      <div style={{ marginBottom: 8 }}>
        <Tag color={typeColors[data.type] || 'default'}>{data.type}</Tag>
        {data.routing_strategy && (
          <Tag>{data.routing_strategy}</Tag>
        )}
      </div>

      {data.description && (
        <div style={{ fontSize: 12, color: '#666', marginTop: 4 }}>
          {data.description.length > 50
            ? `${data.description.substring(0, 50)}...`
            : data.description}
        </div>
      )}

      <Handle type="source" position={Position.Bottom} />
    </div>
  );
};

export default memo(AgentNode);
