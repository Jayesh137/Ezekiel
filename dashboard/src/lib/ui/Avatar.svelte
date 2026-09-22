<script>
	// Gradient identicon: you learn "the purple one" before you read hex.
	// The ring is the stories affordance: seen, unseen (something changed), alert.
	import { avatar } from '$lib/review.js';

	export let address = '';
	export let size = 40;
	/** 'none' | 'seen' | 'unseen' | 'alert' */
	export let ring = 'none';

	$: av = avatar(address);
</script>

<span class="avatar ring-{ring}" style="--size:{size}px" role="img" aria-label="Wallet {address}">
	<span
		class="face"
		style="background: linear-gradient({av.angle}deg, hsl({av.a} 72% 58%), hsl({av.b} 68% 42%))"
	></span>
</span>

<style>
	.avatar {
		width: var(--size);
		height: var(--size);
		border-radius: 50%;
		display: inline-grid;
		place-items: center;
		flex: none;
		box-sizing: border-box;
	}
	.face {
		width: 100%;
		height: 100%;
		border-radius: 50%;
		box-sizing: border-box;
	}
	.ring-seen,
	.ring-unseen,
	.ring-alert {
		padding: 2.5px;
	}
	.ring-seen { background: var(--border); }
	.ring-unseen {
		background: conic-gradient(from 200deg, var(--accent-cyan), var(--accent-purple), var(--accent-cyan));
	}
	.ring-alert { background: var(--accent-red); }
	.ring-seen .face,
	.ring-unseen .face,
	.ring-alert .face {
		border: 2.5px solid var(--bg-primary);
	}
</style>
