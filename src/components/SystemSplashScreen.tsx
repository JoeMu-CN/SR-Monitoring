import React, { useEffect } from 'react';
import { motion } from 'motion/react';

interface SystemSplashScreenProps {
  onComplete: () => void;
}

export const SystemSplashScreen: React.FC<SystemSplashScreenProps> = ({ onComplete }) => {
  useEffect(() => {
    const timer = setTimeout(() => {
      onComplete();
    }, 1800);

    return () => clearTimeout(timer);
  }, [onComplete]);

  return (
    <motion.div
      initial={{ opacity: 1 }}
      exit={{ opacity: 0, scale: 0.98 }}
      transition={{ duration: 0.35, ease: 'easeInOut' }}
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-[#f7f9ff] dark:bg-[#0b131e] text-[#101d28] dark:text-slate-100 select-none p-6"
    >
      <div className="flex flex-col items-center space-y-6">
        {/* Favicon / Logo Badge */}
        <div className="relative flex items-center justify-center w-16 h-16 rounded-2xl bg-[#004782] text-white shadow-xl shadow-[#004782]/20">
          <span className="font-serif text-2xl font-black tracking-tighter">SR</span>
        </div>

        {/* Spinning Dots */}
        <div className="flex items-center gap-2">
          {[0, 1, 2].map((i) => (
            <motion.span
              key={i}
              className="w-2.5 h-2.5 rounded-full bg-[#004782] dark:bg-blue-400"
              animate={{
                scale: [0.7, 1.3, 0.7],
                opacity: [0.3, 1, 0.3],
              }}
              transition={{
                duration: 1,
                repeat: Infinity,
                delay: i * 0.2,
                ease: 'easeInOut',
              }}
            />
          ))}
        </div>
      </div>
    </motion.div>
  );
};

