import React, { useEffect, useState } from 'react';
import PortalChat from './PortalChat';
import { api } from '../api/client';
import type { Portal } from './PortalList';
import styles from '../App.module.css';

const RootPortalChat: React.FC = () => {
  const [portal, setPortal] = useState<Portal | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchPortal = async () => {
      try {
        const data = await api.getDefaultPortal();
        setPortal(data);
      } catch (err) {
        console.error('Failed to fetch default portal', err);
      } finally {
        setLoading(false);
      }
    };
    fetchPortal();
  }, []);

  if (loading) {
    return <div className={styles.placeholder}>加载中...</div>;
  }

  if (!portal) {
    return <div className={styles.placeholder}>无法加载AI助手</div>;
  }

  return <PortalChat portal={portal} onBack={() => {}} hideBackButton={true} />;
};

export default RootPortalChat;
