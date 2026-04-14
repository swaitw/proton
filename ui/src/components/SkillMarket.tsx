import React, { useEffect, useState, useRef } from 'react';
import { FiLayers, FiUploadCloud, FiTrash2, FiUsers, FiClock, FiBox, FiSearch, FiX } from 'react-icons/fi';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api } from '../api/client';
import { useToast } from './ToastProvider';
import styles from './SkillMarket.module.css';

export interface Skill {
  id: string;
  name: string;
  description: string;
  version: string;
  author: string | null;
  tags: string[];
  enabled: boolean;
  installed_at: string;
  agent_count: number;
  readme?: string;
  parameters_schema?: Record<string, any>;
  dependencies?: string[];
}

const CATEGORIES = [
  { id: 'all', label: '全部技能' },
  { id: 'search', label: '搜索与研究', tags: ['search', 'web', 'research', 'google', 'bing'] },
  { id: 'data', label: '数据与分析', tags: ['data', 'database', 'excel', 'sql', 'analysis', 'math'] },
  { id: 'dev', label: '开发与工具', tags: ['dev', 'code', 'github', 'shell', 'python', 'terminal', 'api'] },
  { id: 'productivity', label: '效率与办公', tags: ['productivity', 'office', 'pdf', 'mail', 'calendar', 'doc'] },
  { id: 'media', label: '图像与多媒体', tags: ['image', 'video', 'audio', 'media', 'design'] },
  { id: 'other', label: '其他', tags: [] }
];

const SkillMarket: React.FC = () => {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeCategory, setActiveCategory] = useState('all');
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [displayCount, setDisplayCount] = useState(20);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const { showToast } = useToast();

  const fetchSkills = async () => {
    try {
      setLoading(true);
      const data = await api.listSkills();
      const sortedData = (data as Skill[]).sort((a, b) => b.agent_count - a.agent_count);
      setSkills(sortedData);
    } catch (err: any) {
      const errMsg = err?.response?.data?.detail || err?.message || String(err);
      showToast(errMsg, 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSkills();
  }, []);

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      setUploading(true);
      await api.uploadSkill(file);
      showToast('技能安装成功！', 'success');
      fetchSkills(); // Refresh the list
    } catch (err: any) {
      const errMsg = err?.response?.data?.detail || err?.message || String(err);
      showToast(errMsg, 'error');
    } finally {
      setUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = ''; // Reset input
      }
    }
  };

  const handleDelete = async (e: React.MouseEvent, skillId: string, skillName: string) => {
    e.stopPropagation();
    if (!window.confirm(`确定要卸载并删除技能 "${skillName}" 吗？这可能会影响正在使用它的 Agent。`)) {
      return;
    }

    try {
      await api.uninstallSkill(skillId);
      showToast(`技能 ${skillName} 已卸载`, 'success');
      setSkills(skills.filter((s) => s.id !== skillId));
      if (selectedSkill?.id === skillId) {
        setSelectedSkill(null);
      }
    } catch (err: any) {
      const errMsg = err?.response?.data?.detail || err?.message || String(err);
      showToast(errMsg, 'error');
    }
  };

  const formatDate = (isoString: string) => {
    const date = new Date(isoString);
    return date.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  };

  const filteredSkills = skills.filter(skill => {
    const matchSearch = skill.name.toLowerCase().includes(searchQuery.toLowerCase()) || 
                        skill.description.toLowerCase().includes(searchQuery.toLowerCase());
    
    let matchCategory = true;
    if (activeCategory !== 'all') {
      const cat = CATEGORIES.find(c => c.id === activeCategory);
      if (cat) {
        if (cat.id === 'other') {
          const allPredefinedTags = CATEGORIES.filter(c => c.id !== 'all' && c.id !== 'other').flatMap(c => c.tags || []);
          matchCategory = !skill.tags?.some(tag => allPredefinedTags.includes(tag.toLowerCase()));
        } else {
          matchCategory = skill.tags?.some(tag => cat.tags?.includes(tag.toLowerCase()));
        }
      }
    }
    
    return matchSearch && matchCategory;
  });

  const displayedSkills = filteredSkills.slice(0, displayCount);

  const handleLoadMore = () => {
    setDisplayCount(prev => prev + 20);
  };

  return (
    <div className={styles.container}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>技能库 (Skill Market)</h1>
          <p className={styles.description}>
            管理和安装全局的扩展能力，这些能力可以被所有工作流和 Agent 绑定并复用。
          </p>
        </div>
        <div className={styles.actions}>
          <input
            type="file"
            ref={fileInputRef}
            className={styles.hiddenInput}
            accept=".zip,.skill"
            onChange={handleFileChange}
          />
          <button
            className={styles.uploadBtn}
            onClick={handleUploadClick}
            disabled={uploading}
          >
            <FiUploadCloud size={16} />
            {uploading ? '安装中...' : '上传并安装技能'}
          </button>
        </div>
      </header>

      <div className={styles.layout}>
        <aside className={styles.sidebar}>
          {CATEGORIES.map(cat => (
            <button 
              key={cat.id} 
              className={`${styles.categoryBtn} ${activeCategory === cat.id ? styles.active : ''}`}
              onClick={() => setActiveCategory(cat.id)}
            >
              {cat.label}
            </button>
          ))}
        </aside>

        <main className={styles.mainContent}>
          <div className={styles.toolbar}>
            <div className={styles.searchBox}>
              <FiSearch />
              <input 
                type="text" 
                placeholder="搜索技能名称或描述..." 
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                className={styles.searchInput}
              />
            </div>
          </div>

          {loading ? (
            <div className={styles.loading}>加载中...</div>
          ) : filteredSkills.length === 0 ? (
            <div className={styles.emptyState}>
              <FiBox className={styles.emptyIcon} />
              <h3>未找到相关技能</h3>
              <p>请尝试更改分类或搜索关键词，或者点击右上角安装新技能</p>
            </div>
          ) : (
            <>
              <div className={styles.grid}>
                {displayedSkills.map((skill) => (
                  <div 
                    key={skill.id} 
                    className={styles.card} 
                    onClick={() => setSelectedSkill(skill)}
                  >
                    <div className={styles.cardHeader}>
                      <div className={styles.cardTitle}>
                        <div className={styles.iconWrapper}>
                          <FiLayers />
                        </div>
                        <div>
                          <h3 className={styles.name}>{skill.name}</h3>
                          <span className={styles.version}>v{skill.version}</span>
                        </div>
                      </div>
                      <button
                        className={styles.deleteBtn}
                        onClick={(e) => handleDelete(e, skill.id, skill.name)}
                        title="卸载技能"
                      >
                        <FiTrash2 size={16} />
                      </button>
                    </div>

                    {skill.tags && skill.tags.length > 0 && (
                      <div className={styles.tags}>
                        {skill.tags.map((tag, index) => (
                          <span key={index} className={styles.tag}>
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}

                    <p className={styles.cardDesc}>{skill.description}</p>

                    <div className={styles.cardFooter}>
                      <div className={styles.stat} title="已被多少个 Agent 绑定">
                        <FiUsers />
                        <span>{skill.agent_count} 绑定</span>
                      </div>
                      <div className={styles.stat} title="安装时间">
                        <FiClock />
                        <span className={styles.date}>{formatDate(skill.installed_at)}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
              
              {filteredSkills.length > displayCount && (
                <div className={styles.loadMoreContainer}>
                  <button className={styles.loadMoreBtn} onClick={handleLoadMore}>
                    加载更多
                  </button>
                </div>
              )}
            </>
          )}
        </main>
      </div>

      {selectedSkill && (
        <div className={styles.modalOverlay} onClick={() => setSelectedSkill(null)}>
          <div className={styles.modalContent} onClick={e => e.stopPropagation()}>
            <div className={styles.modalHeader}>
              <div className={styles.modalTitleGroup}>
                <div className={styles.iconWrapper}>
                  <FiLayers />
                </div>
                <div>
                  <h2 className={styles.modalTitle}>{selectedSkill.name}</h2>
                  <span className={styles.version} style={{ marginTop: '4px', display: 'inline-block' }}>v{selectedSkill.version}</span>
                </div>
              </div>
              <button className={styles.closeBtn} onClick={() => setSelectedSkill(null)}>
                <FiX size={20} />
              </button>
            </div>
            <div className={styles.modalBody}>
              <div className={styles.detailSection}>
                <span className={styles.detailLabel}>技能描述</span>
                <p className={styles.detailText}>{selectedSkill.description}</p>
              </div>

              {selectedSkill.readme && (
                <div className={styles.detailSection}>
                  <span className={styles.detailLabel}>SKILL.md / 使用文档</span>
                  <div className={styles.markdownContainer}>
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {selectedSkill.readme}
                    </ReactMarkdown>
                  </div>
                </div>
              )}

              {selectedSkill.parameters_schema && Object.keys(selectedSkill.parameters_schema).length > 0 && (
                <div className={styles.detailSection}>
                  <span className={styles.detailLabel}>参数 Schema</span>
                  <pre className={styles.codeBlock}>
                    {JSON.stringify(selectedSkill.parameters_schema, null, 2)}
                  </pre>
                </div>
              )}

              {selectedSkill.dependencies && selectedSkill.dependencies.length > 0 && (
                <div className={styles.detailSection}>
                  <span className={styles.detailLabel}>系统依赖</span>
                  <div className={styles.tags} style={{ marginBottom: 0 }}>
                    {selectedSkill.dependencies.map((dep, index) => (
                      <span key={index} className={styles.tag}>{dep}</span>
                    ))}
                  </div>
                </div>
              )}
              
              <div className={styles.detailSection}>
                <span className={styles.detailLabel}>标签分类</span>
                <div className={styles.tags} style={{ marginBottom: 0 }}>
                  {selectedSkill.tags && selectedSkill.tags.length > 0 ? (
                    selectedSkill.tags.map((tag, index) => (
                      <span key={index} className={styles.tag}>{tag}</span>
                    ))
                  ) : (
                    <span className={styles.detailText} style={{ color: '#888' }}>暂无标签</span>
                  )}
                </div>
              </div>

              <div className={styles.detailSection}>
                <span className={styles.detailLabel}>作者信息</span>
                <p className={styles.detailText}>{selectedSkill.author || 'Proton Community'}</p>
              </div>

              <div className={styles.detailSection}>
                <span className={styles.detailLabel}>使用情况</span>
                <p className={styles.detailText}>已被 {selectedSkill.agent_count} 个工作流/Agent 绑定使用</p>
              </div>

              <div className={styles.detailSection}>
                <span className={styles.detailLabel}>安装时间</span>
                <p className={styles.detailText}>{formatDate(selectedSkill.installed_at)}</p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default SkillMarket;