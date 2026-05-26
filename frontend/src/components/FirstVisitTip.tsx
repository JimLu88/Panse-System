import { Alert } from 'antd';
import { useEffect, useState } from 'react';

/**
 * 首次进入某页面时显示一条提示, 用户关闭后 localStorage 永久记着不再显示。
 * plan §12.4 操作引导.
 */
export function FirstVisitTip({
  storageKey,
  title,
  description,
}: {
  storageKey: string;
  title: string;
  description: React.ReactNode;
}) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!localStorage.getItem(`panse_tip_${storageKey}`)) {
      setVisible(true);
    }
  }, [storageKey]);

  if (!visible) return null;
  return (
    <Alert
      type="info"
      showIcon
      closable
      message={title}
      description={description}
      onClose={() => {
        localStorage.setItem(`panse_tip_${storageKey}`, '1');
        setVisible(false);
      }}
      style={{ marginBottom: 12 }}
    />
  );
}
