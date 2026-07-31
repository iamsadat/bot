'use client';

import { Canvas, useFrame } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import * as THREE from 'three';

// A slowly-rotating field of points forming a wireframe sphere — the cloud of
// jobs being searched. Pure GPU, no textures, cheap to render.
function ParticleField({ count = 2600 }: { count?: number }) {
  const ref = useRef<THREE.Points>(null);

  const positions = useMemo(() => {
    const arr = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      // Fibonacci sphere for an even distribution, with slight radial jitter.
      const t = i / count;
      const phi = Math.acos(1 - 2 * t);
      const theta = Math.PI * (1 + Math.sqrt(5)) * i;
      const r = 2.5 + (((i * 9301 + 49297) % 233280) / 233280 - 0.5) * 0.6;
      arr[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      arr[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      arr[i * 3 + 2] = r * Math.cos(phi);
    }
    return arr;
  }, [count]);

  useFrame((state, delta) => {
    if (!ref.current) return;
    ref.current.rotation.y += delta * 0.08;
    ref.current.rotation.x = Math.sin(state.clock.elapsedTime * 0.15) * 0.18;
  });

  return (
    <points ref={ref}>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          count={count}
          array={positions}
          itemSize={3}
        />
      </bufferGeometry>
      {/* #c67139 mirrors --color-accent (Grove's terracotta). PointsMaterial
          is unlit — colour is applied directly and scene lights have no
          effect on it — so the old ambient/point lights were dead code and
          are dropped. Normal (not additive) blending plus a lower opacity is
          what keeps the dots from blowing out to white against the cream
          #f5ead8 backdrop the way the additive light-blue version did. */}
      <pointsMaterial
        size={0.026}
        color="#c67139"
        transparent
        opacity={0.6}
        sizeAttenuation
        depthWrite={false}
      />
    </points>
  );
}

export default function Hero3D() {
  return (
    <Canvas
      camera={{ position: [0, 0, 6], fov: 50 }}
      dpr={[1, 2]}
      gl={{ antialias: true, alpha: true }}
      style={{ position: 'absolute', inset: 0 }}
    >
      <ParticleField />
    </Canvas>
  );
}
