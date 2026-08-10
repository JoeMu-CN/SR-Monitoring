import React from 'react';
import { motion } from 'motion/react';

interface UnifiedLoaderProps {
  size?: 'sm' | 'md' | 'lg';
  variant?: 'spinner' | 'dots' | 'ring';
  label?: string;
  className?: string;
}

export const UnifiedLoader: React.FC<UnifiedLoaderProps> = ({
  size = 'md',
  variant = 'ring',
  label,
  className = '',
}) => {
  const sizeMap = {
    sm: { dimension: 'w-4 h-4', dotDimension: 'w-1.5 h-1.5', text: 'text-xs' },
    md: { dimension: 'w-5 h-5', dotDimension: 'w-2 h-2', text: 'text-xs' },
    lg: { dimension: 'w-8 h-8', dotDimension: 'w-2.5 h-2.5', text: 'text-sm' },
  };

  const { dimension, dotDimension, text } = sizeMap[size];

  if (variant === 'dots') {
    return (
      <div className={`inline-flex items-center gap-1.5 ${className}`}>
        {[0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className={`${dotDimension} rounded-full bg-[#007aff] dark:bg-blue-400`}
            animate={{
              scale: [0.7, 1.25, 0.7],
              opacity: [0.35, 1, 0.35],
            }}
            transition={{
              duration: 0.9,
              repeat: Infinity,
              delay: i * 0.18,
              ease: 'easeInOut',
            }}
          />
        ))}
        {label && (
          <span className={`ml-1 font-medium text-slate-500 dark:text-slate-400 ${text}`}>
            {label}
          </span>
        )}
      </div>
    );
  }

  return (
    <div className={`inline-flex items-center gap-2.5 ${className}`}>
      <div className={`relative ${dimension} flex items-center justify-center shrink-0`}>
        {/* Apple-style Smooth Gradient Ring */}
        <svg
          className="animate-spin w-full h-full text-[#007aff] dark:text-blue-400"
          viewBox="0 0 32 32"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
        >
          <circle
            cx="16"
            cy="16"
            r="12"
            stroke="currentColor"
            strokeWidth="3"
            strokeDasharray="60 20"
            strokeLinecap="round"
            className="opacity-90"
          />
          <circle
            cx="16"
            cy="16"
            r="12"
            stroke="currentColor"
            strokeWidth="3"
            className="opacity-20"
          />
        </svg>
      </div>
      {label && (
        <span className={`font-semibold text-slate-700 dark:text-slate-300 ${text}`}>
          {label}
        </span>
      )}
    </div>
  );
};

export const ViewSkeleton: React.FC<{ rows?: number }> = ({ rows = 3 }) => {
  return (
    <div className="w-full space-y-4 animate-pulse">
      {/* KPI Cards Skeleton */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3.5">
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className="h-24 rounded-2xl bg-slate-200/60 dark:bg-slate-800/60 border border-slate-200/80 dark:border-slate-700/60 p-4 space-y-2"
          >
            <div className="h-3 w-1/2 bg-slate-300/70 dark:bg-slate-700/70 rounded-md" />
            <div className="h-6 w-2/3 bg-slate-300/80 dark:bg-slate-700/80 rounded-lg mt-2" />
          </div>
        ))}
      </div>

      {/* Main Container Skeleton */}
      <div className="bg-slate-200/50 dark:bg-slate-800/50 border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-5 space-y-4">
        <div className="h-5 w-1/4 bg-slate-300/70 dark:bg-slate-700/70 rounded-lg" />
        <div className="space-y-3">
          {Array.from({ length: rows }).map((_, idx) => (
            <div
              key={idx}
              className="h-16 rounded-xl bg-slate-300/50 dark:bg-slate-700/50 w-full"
            />
          ))}
        </div>
      </div>
    </div>
  );
};
