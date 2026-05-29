package com.driftshield.backend.entity.alert;


import jakarta.persistence.*;
import lombok.*;

import java.time.LocalDateTime;

@Entity
@Table(name= "alerts")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class Alert {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private  String type;
    private String severity;
    @Column(columnDefinition = "TEXT")
    private String message;
    private LocalDateTime createdAt;
}
