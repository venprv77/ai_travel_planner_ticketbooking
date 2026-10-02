document.addEventListener("DOMContentLoaded", function () {

    // ---------------------------------------------------------------
    // Travel style card selection
    // ---------------------------------------------------------------

    const styleCards = document.querySelectorAll(".style-card");

    styleCards.forEach(function (card) {

        const radio = card.querySelector("input[type='radio']");

        if (!radio) {
            return;
        }

        function updateStyleCards() {

            styleCards.forEach(function (item) {
                item.classList.remove("selected");
            });

            if (radio.checked) {
                card.classList.add("selected");
            }
        }

        radio.addEventListener("change", updateStyleCards);

        updateStyleCards();

    });


    // ---------------------------------------------------------------
    // Multi-passenger booking
    // ---------------------------------------------------------------

    const addPassengerButton = document.getElementById("addPassenger");
    const passengerList = document.getElementById("passengerList");

    if (addPassengerButton && passengerList) {

        addPassengerButton.addEventListener("click", function () {

            const rows = passengerList.querySelectorAll(".passenger-row");

            if (rows.length >= 10) {
                alert("Maximum 10 passengers per booking.");
                return;
            }

            const number = rows.length + 1;

            const row = document.createElement("div");

            row.className = "passenger-row";

            row.innerHTML = `
                <div class="form-group">

                    <label>Passenger Name</label>

                    <input
                        type="text"
                        name="passenger_name"
                        placeholder="Passenger ${number}"
                        required
                    >

                </div>


                <div class="form-group">

                    <label>Seat</label>

                    <select
                        name="seat_number"
                        class="seat-select"
                        required
                    >

                        <option value="">
                            Select seat
                        </option>

                        ${Array.from(
                            { length: 40 },
                            (_, index) =>
                                `<option value="${index + 1}">
                                    Seat ${index + 1}
                                </option>`
                        ).join("")}

                    </select>

                </div>


                <button
                    type="button"
                    class="remove-passenger"
                >
                    Remove
                </button>
            `;

            passengerList.appendChild(row);

            updateSeatOptions();

        });


        passengerList.addEventListener("click", function (event) {

            if (!event.target.classList.contains("remove-passenger")) {
                return;
            }

            event.target.closest(".passenger-row").remove();

            updateSeatOptions();

        });


        passengerList.addEventListener("change", function (event) {

            if (event.target.classList.contains("seat-select")) {
                updateSeatOptions();
            }

        });


        function updateSeatOptions() {

            const selects =
                passengerList.querySelectorAll(".seat-select");

            const selectedSeats = new Set();

            selects.forEach(function (select) {

                if (select.value) {
                    selectedSeats.add(select.value);
                }

            });


            selects.forEach(function (select) {

                Array.from(select.options).forEach(function (option) {

                    if (!option.value) {
                        return;
                    }

                    option.disabled =
                        selectedSeats.has(option.value) &&
                        option.value !== select.value;

                });

            });

        }

    }


    // ---------------------------------------------------------------
    // Confirmation before destructive actions
    // ---------------------------------------------------------------

    document.addEventListener("click", function (event) {

        const element = event.target.closest("[data-confirm]");

        if (!element) {
            return;
        }

        const message = element.getAttribute("data-confirm");

        if (!confirm(message)) {
            event.preventDefault();
        }

    });


    // ---------------------------------------------------------------
    // Automatically hide flash messages
    // ---------------------------------------------------------------

    setTimeout(function () {

        document.querySelectorAll(".flash").forEach(function (flash) {

            flash.style.opacity = "0";

            setTimeout(function () {
                flash.remove();
            }, 400);

        });

    }, 5000);

});
