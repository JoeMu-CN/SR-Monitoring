import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useState} from 'react';
import {describe, expect, it} from 'vitest';

const Toggle = () => {
  const [pressed, setPressed] = useState(false);

  return <button aria-pressed={pressed} onClick={() => setPressed((value) => !value)} type="button" />;
};

describe('frontend test harness', () => {
  it('updates a DOM control through a user interaction', async () => {
    const user = userEvent.setup();
    render(<Toggle />);

    const control = screen.getByRole('button');
    expect(control).toBeInTheDocument();
    expect(control).toHaveAttribute('aria-pressed', 'false');

    await user.click(control);

    expect(control).toHaveAttribute('aria-pressed', 'true');
  });
});
