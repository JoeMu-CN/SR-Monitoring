import React, { useEffect } from 'react';
import { motion } from 'motion/react';
import { UnifiedLoader } from './common/UnifiedLoader';

interface SystemSplashScreenProps {
  onComplete: () => void;
}

export const SystemSplashScreen: React.FC<SystemSplashScreenProps> = ({ onComplete }) => {
  useEffect(() => {
    const timer = setTimeout(() => {
      onComplete();
    }, 1200);

    return () => clearTimeout(timer);
  }, [onComplete]);

  return (
    <motion.div
      initial={{ opacity: 1 }}
      exit={{ opacity: 0, scale: 0.98 }}
      transition={{ duration: 0.35, ease: 'easeInOut' }}
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-slate-100/90 dark:bg-[#0b131e] text-[#101d28] dark:text-slate-100 select-none p-6 backdrop-blur-md"
    >
      <div className="flex flex-col items-center space-y-5">
        {/* Favicon / Logo Badge */}
        <motion.div
          initial={{ scale: 0.9, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ duration: 0.3 }}
          className="relative flex items-center justify-center w-16 h-16 rounded-2xl bg-[#007aff] text-white shadow-xl shadow-[#007aff]/25"
        >
          <span className="font-serif text-2xl font-black tracking-tighter">SR</span>
        </motion.div>

        {/* Unified Loading Animation */}
        <UnifiedLoader size="lg" label="系统引擎初始化中..." />
      </div>
    </motion.div>
  );
};


