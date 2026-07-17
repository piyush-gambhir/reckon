'use client';

import { gsap } from 'gsap';
import { CustomEase } from 'gsap/CustomEase';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger, CustomEase);
CustomEase.create('home-default', '0.625,0.05,0,1');

export { gsap, ScrollTrigger };
