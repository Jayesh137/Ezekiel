<script>
	// One wallet address, rendered once, everywhere.
	//
	// The href always carries the FULL address even when the label is shortened —
	// linking the visible "0x45d2...4029" was the failure mode this component
	// exists to make impossible. An address that is empty, partial or malformed
	// renders as plain text, never as a dead link.
	import { addressUrl, shortAddr } from '$lib/api.js';

	/** @type {string|null|undefined} */
	export let address = '';
	/** Show the address in full instead of shortened. */
	export let full = false;
	/** Optional label override; the href still uses `address`. */
	export let label = '';
	/** Extra classes, so existing styling is preserved at each call site. */
	export let className = 'mono';
	/** Stop the click bubbling to a parent row/card toggle. */
	export let stopPropagation = false;

	$: href = addressUrl(address);
	$: text = label || (full ? address : shortAddr(address)) || '—';

	function onClick(e) {
		if (stopPropagation) e.stopPropagation();
	}
</script>

{#if href}
	<a
		{href}
		class={className}
		target="_blank"
		rel="noopener noreferrer"
		title="{address} — view on Hypurrscan"
		on:click={onClick}>{text}</a
	>
{:else}
	<span class={className} title={address || ''}>{text}</span>
{/if}
