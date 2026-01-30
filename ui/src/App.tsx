import React, { useState } from 'react';
import { Layout, Menu, Typography } from 'antd';
import {
  AppstoreOutlined,
  PlayCircleOutlined,
  SettingOutlined,
  ApiOutlined,
} from '@ant-design/icons';
import WorkflowEditor from './components/WorkflowEditor';
import WorkflowList from './components/WorkflowList';

const { Header, Sider, Content } = Layout;
const { Title } = Typography;

const App: React.FC = () => {
  const [selectedMenu, setSelectedMenu] = useState('editor');
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);

  const menuItems = [
    {
      key: 'editor',
      icon: <AppstoreOutlined />,
      label: 'Workflow Editor',
    },
    {
      key: 'workflows',
      icon: <PlayCircleOutlined />,
      label: 'Workflows',
    },
    {
      key: 'plugins',
      icon: <ApiOutlined />,
      label: 'Plugins',
    },
    {
      key: 'settings',
      icon: <SettingOutlined />,
      label: 'Settings',
    },
  ];

  const renderContent = () => {
    switch (selectedMenu) {
      case 'editor':
        return <WorkflowEditor workflowId={selectedWorkflowId} />;
      case 'workflows':
        return (
          <WorkflowList
            onSelect={(id) => {
              setSelectedWorkflowId(id);
              setSelectedMenu('editor');
            }}
          />
        );
      case 'plugins':
        return <div>Plugins management coming soon...</div>;
      case 'settings':
        return <div>Settings coming soon...</div>;
      default:
        return null;
    }
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', padding: '0 24px' }}>
        <Title level={4} style={{ color: 'white', margin: 0 }}>
          Proton Agent Platform
        </Title>
      </Header>
      <Layout>
        <Sider width={200} theme="light">
          <Menu
            mode="inline"
            selectedKeys={[selectedMenu]}
            items={menuItems}
            onClick={({ key }) => setSelectedMenu(key)}
            style={{ height: '100%' }}
          />
        </Sider>
        <Content style={{ padding: 24, background: '#f5f5f5' }}>
          {renderContent()}
        </Content>
      </Layout>
    </Layout>
  );
};

export default App;
