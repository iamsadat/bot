'use client';

import { useEffect } from 'react';
import { animate, useMotionValue, useTransform, motion } from 'framer-motion';

export default function AnimatedNumber({ value }: { value: number }) {
  const mv = useMotionValue(0);
  const rounded = useTransform(mv, (v) => Math.round(v).toString());
  useEffect(() => {
    const controls = animate(mv, value, { duration: 0.7, ease: 'easeOut' });
    return controls.stop;
  }, [value, mv]);
  return <motion.span>{rounded}</motion.span>;
}
