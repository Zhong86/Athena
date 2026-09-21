/** Premade quotes for the dashboard header. Picked deterministically by day
 * so the greeting is stable across a single day's page loads (SSR has no
 * client-side state to remember a "shuffled" pick from). */

export type Quote = { text: string; author: string };

export const QUOTES: Quote[] = [
  { text: "The expert in anything was once a beginner.", author: "Helen Hayes" },
  { text: "It always seems impossible until it's done.", author: "Nelson Mandela" },
  { text: "We are what we repeatedly do.", author: "Will Durant" },
  { text: "Small daily improvements are the key to staggering long-term results.", author: "Robin Sharma" },
  { text: "The beautiful thing about learning is that no one can take it away from you.", author: "B.B. King" },
  { text: "You don't have to be great to start, but you have to start to be great.", author: "Zig Ziglar" },
  { text: "Discipline is choosing between what you want now and what you want most.", author: "Abraham Lincoln" },
  { text: "Progress, not perfection.", author: "Unknown" },
  { text: "The only way to learn mathematics is to do mathematics.", author: "Paul Halmos" },
  { text: "Well done is better than well said.", author: "Benjamin Franklin" },
  { text: "Success is the sum of small efforts, repeated day in and day out.", author: "Robert Collier" },
  { text: "Study without desire spoils the memory, and it retains nothing that it takes in.", author: "Leonardo da Vinci" },
  { text: "The mind is not a vessel to be filled but a fire to be kindled.", author: "Plutarch" },
  { text: "Don't watch the clock; do what it does. Keep going.", author: "Sam Levenson" },
];

function dayOfYear(date: Date): number {
  const start = Date.UTC(date.getUTCFullYear(), 0, 1);
  const diff = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()) - start;
  return Math.floor(diff / 86_400_000);
}

export function quoteOfTheDay(date: Date = new Date()): Quote {
  return QUOTES[dayOfYear(date) % QUOTES.length];
}
