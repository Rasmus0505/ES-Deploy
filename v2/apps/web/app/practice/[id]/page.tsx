import PracticeClient from './PracticeClient';

export default async function PracticePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PracticeClient exerciseId={id} />;
}
